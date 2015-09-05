import logging

from xpcom import components
import koLintResult
from koLintResult import KoLintResult, SEV_ERROR, SEV_WARNING, SEV_INFO
from koLintResult import createAddResult as add_result
from koLintResults import koLintResults

import os, sys, re, which
import tempfile
import process
import koprocessutils
import xml

log = logging.getLogger('koffeelint')

MSG_NO_COFFEELINT = "coffeelint executable not found on the environment PATH. Is it installed correctly?"
MSG_NO_PATH = "Cannot access environment PATH. Is the system configured correctly?"
MSG_XML_EXCEPTION = "Error parsing coffeelint results. See Komodo log for details."
MSG_XML_PARSE_ERROR = "Error parsing coffeelint results. Is %s a valid JSON file?"
MSG_RUN_EXCEPTION = "Error running coffeelint: %s"
# Don't complain about permanent problems more than once. Instead,
# stash the message here the first time and check for it thereafter.

_complained = {}

jslint_reason_re = re.compile(r'^\[(.*?)\]\s*(.*)')
jslint_error_re = re.compile(r'\[.*?\]:\d+:\d+:\s* .*?:(.*?)\s*\^\s*')

# Mostly boilerplate linter code, lifted from koCoffeeScriptLinter.

class CoffeeLintRequest(object):
	def __init__(self, user_path, request, text):
		self.valid = True
		self.results = koLintResults()
		self.request = request
		self.text = text
		self.cwd = request.cwd

		if self.text:
			self.textlines = text.splitlines()
		else:
			self.textlines = [""]
			self.valid = False

		self.user_path = user_path

		if not user_path:
			self.add_internal_error(MSG_NO_PATH)
			self.valid = False

		self.coffeelint_exe = find_coffeelint(self.user_path)

		if not self.coffeelint_exe:
			self.add_internal_error(MSG_NO_COFFEELINT)
			self.valid = False

		# CoffeeLint looks for a coffeelint.json config file somewhere at or
		# above the directory of the input file. Unfortunately, we're
		# loading from tmp, not cwd.
		# The user still expects the config file in cwd (or above) to be honored,
		# so we have to do the searching ourselves. :S

		self.configfile = find_filename(self.cwd, "coffeelint.json")

	def run(self):
		if self.valid and self.request.prefset.getBooleanPref("lint_coffee_script"):
			success, stdin = self.run_coffeelint()

			if success:
				self.parse_stdin(stdin)
			else:
				self.add_internal_error(MSG_RUN_EXCEPTION % stdin)

		return self.results

	def parse_stdin(self, stdin):
		try:
			xml.sax.parseString(stdin,
								JslintXmlHandler(self),
								ErrorXmlHandler(self))
			log.warn("Parsed coffeelint result successfully")
		except Exception, e:
			log.exception("Could not parse coffeelint result: %s", e);
			self.add_internal_error(MSG_XML_EXCEPTION)

	def run_coffeelint(self):

		tmpfilename = create_tempfile(self.text)
		cmd = None

		if self.configfile == None:
			cmd = [self.coffeelint_exe, "--color=never", "--reporter", "jslint", tmpfilename]
		else:
			cmd = [self.coffeelint_exe, "--color=never", "-f", self.configfile, "--reporter", "jslint", tmpfilename]

		success = None
		stdin = None
		try:
			p = process.ProcessOpen(cmd, cwd=self.cwd)
			stdin, stderr = p.communicate()
			if stderr and len(stderr) > 0:
				log.error("Error returned from coffeelint: " + stderr);
				stdin = stderr
				success = False
			else:
				success = True
		except Exception, e:
			log.exception("Problem running %s", self.coffeelint_exe)
			stdin = str(e)
			success = False
		finally:
			os.unlink(tmpfilename)

		return success, stdin

	def add_internal_error(self, desc):
		# createAddResult ignores results (!) if they occur on empty lines.
		# Since internal errors inherently have no line to point to,
		# we need to find a good line to pass to createAddResult.
		# If we can't find a good line, fake it.
		lineNo = 0
		use_fake_lines = True

		# This is the opposite of what takes place in createAddResult
		while lineNo < 50 \
			and lineNo < len(self.textlines):

			if len(self.textlines[lineNo]) == 0:
				lineNo += 1
			else:
				use_fake_lines = False
				break

		if use_fake_lines:
			fakelines = ["                        "]
			add_result(self.results, fakelines, SEV_ERROR, 1, desc)
		else:
			add_result(self.results, self.textlines, SEV_ERROR, lineNo + 1, desc)

	def add_result(self, severity, lineNo, desc):
		add_result(self.results, self.textlines, severity, lineNo, desc)

class ElCoffeeScriptLinter(object):
	_com_interfaces_ = [components.interfaces.koILinter]
	_reg_clsid_ = "{2FC771E6-51EB-11E5-916D-6121D5902334}"
	_reg_contractid_ = "@ervumlens.github.io/elCoffeeScriptLinter;1"
	_reg_categories_ = [
		 ("category-komodo-linter", 'CoffeeScript'),
		 ]

	def lint(self, request):
		text = request.content.encode(request.encoding.python_encoding_name)
		return self.lint_with_text(request, text)

	def lint_with_text(self, request, text):

		lint_request = CoffeeLintRequest(find_user_path(), request, text)
		return lint_request.run()


def find_coffeelint(path):
	coffeelintExe = None
	try:
		coffeelintExe = which.which("coffeelint", path=path)
		if coffeelintExe:
			if sys.platform.startswith("win") and os.path.exists(coffeelintExe + ".cmd"):
				coffeelintExe += ".cmd"
	except which.WhichError:
		msg = "coffeelint not found"
		if msg not in _complained:
			_complained[msg] = None
			log.error(msg)

	return coffeelintExe

def find_user_path():
	try:
		return koprocessutils.getUserEnv()["PATH"].split(os.pathsep)
	except:
		msg = "can't get user path"
		if msg not in _complained:
			_complained[msg] = None
			log.exception(msg)
		return None


# Shamelessly lifted from https://github.com/Komodo/komodo-editorconfig/blob/master/pylib/editorconfig/handler.py

def find_filename(path, filename):
	"""
	Return full filepath for existing filename in
	the first directory in or above path, or None if not found.
	"""
	first_filename = None
	cur_path = path
	while True:
		cur_filename = os.path.join(cur_path, filename)

		if os.path.isfile(cur_filename):
			first_filename = cur_filename
			break

		new_path = os.path.dirname(cur_path)
		if cur_path == new_path:
			break

		cur_path = new_path

	return first_filename

def create_tempfile(content):
		tmpfilename = tempfile.mktemp() + '.coffee'
		fout = open(tmpfilename, 'wb')
		fout.write(content)
		fout.close()
		return tmpfilename

def jslint_severity(string):
	if string == "error":
		return SEV_ERROR
	elif string == "warn":
		return SEV_WARNING
	else:
		return SEV_INFO

def jslint_description(msg, evidence, line):

	m = jslint_error_re.match(msg)
	if m:
		# Compiler errors need additional grooming since
		# they contain redundant information and a copy
		# of the text from the problematic line.
		msg = m.group(1)
		msg = msg.replace(line, "")

	if evidence != "undefined":
		msg = msg + " : " + evidence

	return msg

class ErrorXmlHandler(xml.sax.handler.ErrorHandler):
	def __init__(self, request):
		self.request = request

	def fatalError(self, exception):
		# Bad return from coffeelint? Assume it's a config error.
		self.request.add_internal_error(
			MSG_XML_PARSE_ERROR % str(self.request.configfile))

class JslintXmlHandler(xml.sax.handler.ContentHandler):
	def __init__(self, request):
			self.request = request

	def startElement(self, name, attrs):
		if name == "issue":
			reason = attrs["reason"]
			m = jslint_reason_re.match(reason)
			if m:
				self.translate_issue(m, attrs)

	def translate_issue(self, reasonMatch, attrs):
		lineStart = int(attrs["line"])
		evidence = attrs["evidence"]
		rawSeverity = reasonMatch.group(1)
		rawMessage = reasonMatch.group(2)
		severity = jslint_severity(rawSeverity)
		line = ""
		if lineStart > 0:
			line = self.request.textlines[lineStart - 1]
		description = jslint_description(rawMessage, evidence, line)
		self.request.add_result(severity, lineStart, description)

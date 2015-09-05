import logging

from xpcom import components
import koLintResult
from koLintResult import createAddResult, KoLintResult, SEV_ERROR, SEV_WARNING, SEV_INFO
from koLintResults import koLintResults

import os, sys, re, which
import tempfile
import process
import koprocessutils
import xml

log = logging.getLogger('koffeelint')

_complained = {}

jslint_reason_re = re.compile(r'^\[(.*?)\]\s*(.*)')
jslint_error_re = re.compile(r'\[.*?\]:\d+:\d+:\s* .*?:(.*?)\s*\^\s*')

# Shamelessly lifted from https://github.com/Komodo/komodo-editorconfig/blob/master/pylib/editorconfig/handler.py

def find_filename(path, filename):
	"""
	Yield full filepath for existing filename in the first directory in and above path,
	or None if not found.
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

class ElCoffeeScriptLinter():
	_com_interfaces_ = [components.interfaces.koILinter]
	_reg_clsid_ = "{2FC771E6-51EB-11E5-916D-6121D5902334}"
	_reg_contractid_ = "@ervumlens.github.io/elCoffeeScriptLinter;1"
	_reg_categories_ = [
		 ("category-komodo-linter", 'CoffeeScript'),
		 ]

	def __init__(self):
		try:
			self._userPath = koprocessutils.getUserEnv()["PATH"].split(os.pathsep)
		except:
			msg = "can't get user path"
			if msg not in _complained:
				_complained[msg] = None
				log.exception(msg)
			self._userPath = None

	def lint(self, request):
		text = request.content.encode(request.encoding.python_encoding_name)
		return self.lint_with_text(request, text)

	def lint_with_text(self, request, text):
		if not text:
			return None
		prefset = request.prefset
		if not prefset.getBooleanPref("lint_coffee_script"):
			return
		try:
			coffeelintExe = which.which("coffeelint", path=self._userPath)
			if not coffeelintExe:
				return
			if sys.platform.startswith("win") and os.path.exists(coffeelintExe + ".cmd"):
				coffeelintExe += ".cmd"
		except which.WhichError:
			msg = "coffeelint not found"
			if msg not in _complained:
				_complained[msg] = None
				log.error(msg)
			return

		tmpfilename = tempfile.mktemp() + '.coffee'
		fout = open(tmpfilename, 'wb')
		fout.write(text)
		fout.close()

		textlines = text.splitlines()
		cwd = request.cwd

		# CoffeeLint looks for a coffeelint.json config file somewhere at or
		# above the directory of the input file. Unfortunately, we're
		# loading from tmp, not cwd.
		# The user still expects the config file in cwd (or above) to be honored,
		# so we have to do the searching ourselves. :S

		configfile = find_filename(cwd, "coffeelint.json")

		cmd = None

		if configfile == None:
			cmd = [coffeelintExe, "--color=never", "--reporter", "jslint", tmpfilename]
		else:
			cmd = [coffeelintExe, "--color=never", "-f", configfile, "--reporter", "jslint", tmpfilename]

		try:
			# We only need the stdin result.
			p = process.ProcessOpen(cmd, cwd=cwd, stderr=None)
			stdin, _ = p.communicate()
		except:
			log.exception("Problem running %s", coffeelintExe)
		finally:
			os.unlink(tmpfilename)
			pass

		results = koLintResults()
		try:
			xml.sax.parseString(stdin,
								jslintXmlHandler(results, textlines),
								errorXmlHandler(results, textlines, configfile))
		except:
			log.exception("Could not parse coffeelint result");

		return results

def jslint_severity(string):
	if string == "error":
		return SEV_ERROR
	elif string == "warn":
		return SEV_WARNING
	else:
		return SEV_INFO

def jslint_description(msg, evidence, line):

	# Compiler errors need additional grooming.

	m = jslint_error_re.match(msg)
	if m:
		msg = m.group(1)
		msg = msg.replace(line, "")

	if evidence != "undefined":
		msg = msg + " : " + evidence

	return msg

class errorXmlHandler(xml.sax.handler.ErrorHandler):
	def __init__(self, results, textlines, configfile):
		self.results = results
		self.textlines = textlines
		self.configfile = configfile

	def fatalError(self, exception):
		# Bad return from coffeelint? Assume it's a config error.
		self.addResult(SEV_ERROR, 1, "Error parsing coffeelint results. Is " + str(self.configfile) + " a valid JSON file?")

	def addResult(self, severity, lineNo, desc):
		createAddResult(self.results,
						self.textlines,
						severity,
						lineNo,
						desc)

class jslintXmlHandler(xml.sax.handler.ContentHandler):
	def __init__(self, results, textlines):
			self.results = results
			self.textlines = textlines

	def startElement(self, name, attrs):
		if name == "issue":
			reason = attrs["reason"]
			m = jslint_reason_re.match(reason)
			if m:
				lineStart = int(attrs["line"])
				evidence = attrs["evidence"]
				rawSeverity = m.group(1)
				rawMessage = m.group(2)
				severity = jslint_severity(rawSeverity)
				line = ""
				if lineStart > 0:
					line = self.textlines[lineStart - 1]
				description = jslint_description(rawMessage, evidence, line)
				self.addResult(severity, lineStart, description)

	def addResult(self, severity, lineNo, desc):
		createAddResult(self.results,
						self.textlines,
						severity,
						lineNo,
						desc)

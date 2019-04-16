import jedi
import json
import os
import sys

import sourcetraildb as srctrl
from jedi._compatibility import all_suffixes
from _version import __version__
from _version import _sourcetrail_db_version


_virtualFilePath = 'virtual_file.py'


def getEnvironment(environmentDirectoryPath = None):
	if environmentDirectoryPath is not None:
		try:
			environment = jedi.create_environment(environmentDirectoryPath)
			environment._get_subprocess() # check if this environment is really functional
			return environment
		except Exception:
			print('WARNING: The provided environment path "' + environmentDirectoryPath + '" does not specify a functional Python '
				'environment. Using fallback environment instead.')
			pass

	try:
		environment = jedi.get_default_environment()
		environment._get_subprocess() # check if this environment is really functional
		return environment
	except Exception:
		pass

	try:
		for environment in jedi.find_system_environments():
			return environment
	except Exception:
		pass

	if os.name == 'nt': # this is just a workaround and shall be removed once Jedi is fixed (Pull request https://github.com/davidhalter/jedi/pull/1282)
		for version in jedi.api.environment._SUPPORTED_PYTHONS:
			for exe in jedi.api.environment._get_executables_from_windows_registry(version):
				try:
					return jedi.api.environment.Environment(exe)
				except jedi.InvalidPythonEnvironment:
					pass

	raise jedi.InvalidPythonEnvironment("Unable to find an executable Python environment.")


def isSourcetrailDBVersionCompatible(allowLogging = False):
	requiredVersion = _sourcetrail_db_version

	try:
		usedVersion = srctrl.getVersionString()
	except AttributeError:
		if allowLogging:
			print('ERROR: Used version of SourcetrailDB is incompatible to what is required by this version of SourcetrailPythonIndexer (' + requiredVersion + ').')
		return False

	if usedVersion != requiredVersion:
		if allowLogging:
			print('ERROR: Used version of SourcetrailDB (' + usedVersion + ') is incompatible to what is required by this version of SourcetrailPythonIndexer (' + requiredVersion + ').')
		return False
	return True


def indexSourceCode(sourceCode, workingDirectory, astVisitorClient, isVerbose, sysPath = None):
	sourceFilePath = _virtualFilePath

	environment = getEnvironment()

	project = jedi.api.project.Project(workingDirectory, environment = environment)

	evaluator = jedi.evaluate.Evaluator(
		project,
		environment=environment,
		script_path=workingDirectory
	)

	module_node = evaluator.parse(
		code=sourceCode,
		path=workingDirectory,
		cache=False,
		diff_cache=False
	)

	if (isVerbose):
		astVisitor = VerboseAstVisitor(astVisitorClient, evaluator, sourceFilePath, sourceCode, sysPath)
	else:
		astVisitor = AstVisitor(astVisitorClient, evaluator, sourceFilePath, sourceCode, sysPath)

	astVisitor.traverseNode(module_node)


def indexSourceFile(sourceFilePath, environmentDirectoryPath, workingDirectory, astVisitorClient, isVerbose):

	if isVerbose:
		print('INFO: Indexing source file "' + sourceFilePath + '".')

	sourceCode = ''
	with open(sourceFilePath, 'r') as input:
		sourceCode=input.read()

	environment = getEnvironment(environmentDirectoryPath)

	if isVerbose:
		print('INFO: Using Python environment at "' + environment.path + '" for indexing.')

	project = jedi.api.project.Project(workingDirectory, environment = environment)

	evaluator = jedi.evaluate.Evaluator(
		project,
		environment=environment,
		script_path=workingDirectory
	)

	module_node = evaluator.parse(
		code=sourceCode,
		path=workingDirectory,
		cache=False,
		diff_cache=False
	)

	if (isVerbose):
		astVisitor = VerboseAstVisitor(astVisitorClient, evaluator, sourceFilePath)
	else:
		astVisitor = AstVisitor(astVisitorClient, evaluator, sourceFilePath)

	astVisitor.traverseNode(module_node)


class ContextInfo:

	def __init__(self, id, name, node):
		self.id = id
		self.name = name
		self.node = node


class AstVisitor:

	def __init__(self, client, evaluator, sourceFilePath, sourceFileContent = None, sysPath = None):

		self.client = client
		self.environment = evaluator.environment

		self.sourceFilePath = sourceFilePath
		if sourceFilePath != _virtualFilePath:
			self.sourceFilePath = os.path.abspath(self.sourceFilePath)

		self.sourceFileName = os.path.split(self.sourceFilePath)[-1]
		self.sourceFileContent = sourceFileContent

		packageRootPath = os.path.dirname(self.sourceFilePath)
		while os.path.exists(os.path.join(packageRootPath, '__init__.py')):
			packageRootPath =  os.path.dirname(packageRootPath)
		self.sysPath = [packageRootPath]

		if sysPath is not None:
			self.sysPath.extend(sysPath)
		else:
			baseSysPath = evaluator.project._get_base_sys_path(self.environment)
			baseSysPath.sort(reverse=True)
			self.sysPath.extend(baseSysPath)
		self.sysPath = list(filter(None, self.sysPath))

		self.contextStack = []

		fileId = self.client.recordFile(self.sourceFilePath)
		if fileId == 0:
			print('ERROR: ' + srctrl.getLastError())
		self.client.recordFileLanguage(fileId, 'python')
		self.contextStack.append(ContextInfo(fileId, self.sourceFilePath, None))

		moduleNameHierarchy = self.getNameHierarchyFromModuleFilePath(self.sourceFilePath)
		if moduleNameHierarchy is not None:
			moduleId = self.client.recordSymbol(moduleNameHierarchy)
			self.client.recordSymbolDefinitionKind(moduleId, srctrl.DEFINITION_EXPLICIT)
			self.client.recordSymbolKind(moduleId, srctrl.SYMBOL_MODULE)
			self.contextStack.append(ContextInfo(moduleId, moduleNameHierarchy.getDisplayString(), None))


	def traverseNode(self, node):
		if node is None:
			return

		if node.type == 'classdef':
			self.beginVisitClassdef(node)
		elif node.type == 'funcdef':
			self.beginVisitFuncdef(node)
		if node.type == 'import_from':
			self.beginVisitImportFrom(node)
		if node.type == 'import_name':
			self.beginVisitImportName(node)
		elif node.type == 'name':
			self.beginVisitName(node)
		elif node.type == 'string':
			self.beginVisitString(node)
		elif node.type == 'error_leaf':
			self.beginVisitErrorLeaf(node)

		if hasattr(node, 'children'):
			for c in node.children:
				self.traverseNode(c)

		if node.type == 'classdef':
			self.endVisitClassdef(node)
		elif node.type == 'funcdef':
			self.endVisitFuncdef(node)
		if node.type == 'import_from':
			self.endVisitImportFrom(node)
		if node.type == 'import_name':
			self.endVisitImportName(node)
		elif node.type == 'name':
			self.endVisitName(node)
		elif node.type == 'string':
			self.endVisitString(node)
		elif node.type == 'error_leaf':
			self.endVisitErrorLeaf(node)


	def beginVisitClassdef(self, node):
		nameNode = getFirstDirectChildWithType(node, 'name')

		symbolNameHierarchy = self.getNameHierarchyOfNode(nameNode, self.sourceFilePath)
		if symbolNameHierarchy is None:
			symbolNameHierarchy = getNameHierarchyForUnsolvedSymbol()

		symbolId = self.client.recordSymbol(symbolNameHierarchy)
		self.client.recordSymbolDefinitionKind(symbolId, srctrl.DEFINITION_EXPLICIT)
		self.client.recordSymbolKind(symbolId, srctrl.SYMBOL_CLASS)
		self.client.recordSymbolLocation(symbolId, getSourceRangeOfNode(nameNode))
		self.client.recordSymbolScopeLocation(symbolId, getSourceRangeOfNode(node))
		self.contextStack.append(ContextInfo(symbolId, symbolNameHierarchy.getDisplayString(), node))


	def endVisitClassdef(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def beginVisitFuncdef(self, node):
		nameNode = getFirstDirectChildWithType(node, 'name')

		symbolNameHierarchy = self.getNameHierarchyOfNode(nameNode, self.sourceFilePath)
		if symbolNameHierarchy is None:
			symbolNameHierarchy = getNameHierarchyForUnsolvedSymbol()

		symbolId = self.client.recordSymbol(symbolNameHierarchy)
		self.client.recordSymbolDefinitionKind(symbolId, srctrl.DEFINITION_EXPLICIT)
		self.client.recordSymbolKind(symbolId, srctrl.SYMBOL_FUNCTION)
		self.client.recordSymbolLocation(symbolId, getSourceRangeOfNode(nameNode))
		self.client.recordSymbolScopeLocation(symbolId, getSourceRangeOfNode(node))
		self.contextStack.append(ContextInfo(symbolId, symbolNameHierarchy.getDisplayString(), node))


	def endVisitFuncdef(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def beginVisitImportName(self, node):
		self.recordErrorsForUnsolvedImports(node)


	def endVisitImportName(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def beginVisitImportFrom(self, node):
		self.recordErrorsForUnsolvedImports(node)


	def endVisitImportFrom(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def beginVisitName(self, node):
		if len(self.contextStack) == 0:
			return

		if node.value in ['True', 'False', 'None']: # these are not parsed as "keywords" in Python 2
			return

		for definition in self.getDefinitionsOfNode(node, self.sourceFilePath):
			if definition is None:
				continue

			if definition.type == 'instance':
				if definition.line is None and definition.column is None:
					if self.recordInstanceReference(node, definition):
						return

			elif definition.type == 'module':
				if self.recordModuleReference(node, definition):
					return

			elif definition.type in ['class', 'function']:
				(startLine, startColumn) = node.start_pos
				if definition.line == startLine and definition.column == startColumn:
					# Early exit. We don't record references for locations of classes or functions that are definitions
					return

				if definition.type == 'class':
					if self.recordClassReference(node, definition):
						return

				elif definition.type == 'function':
					if self.recordFunctionReference(node, definition):
						return

			elif definition.type == 'param':
				if definition.line is None or definition.column is None:
					# Early skip and try next definition. For now we don't record references for names that don't have a valid definition location
					continue

				if self.recordParamReference(node, definition):
					return

			elif definition.type == 'statement':
				if definition.line is None or definition.column is None:
					# Early skip and try next definition. For now we don't record references for names that don't have a valid definition location
					continue

				if self.recordStatementReference(node, definition):
					return

		referencedSymbolId = self.client.recordSymbol(getNameHierarchyForUnsolvedSymbol())
		referenceId = self.client.recordReference(
			self.contextStack[-1].id,
			referencedSymbolId,
			srctrl.REFERENCE_USAGE
		)
		self.client.recordReferenceLocation(referenceId, getSourceRangeOfNode(node))


	def endVisitName(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def beginVisitString(self, node):
		sourceRange = getSourceRangeOfNode(node)
		if sourceRange.startLine != sourceRange.endLine:
			self.client.recordAtomicSourceRange(sourceRange)


	def endVisitString(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def beginVisitErrorLeaf(self, node):
		self.client.recordError('Unexpected token of type "' + node.token_type + '" encountered.', False, getSourceRangeOfNode(node))


	def endVisitErrorLeaf(self, node):
		if len(self.contextStack) > 0:
			contextNode = self.contextStack[-1].node
			if node == contextNode:
				self.contextStack.pop()


	def recordErrorsForUnsolvedImports(self, node):
		if node.type == 'import_from':
			for c in node.children:
				if self.recordErrorsForUnsolvedImports(c) is False:
					return False
		elif node.type == 'import_as_names':
			for c in node.children:
				self.recordErrorsForUnsolvedImports(c)
		elif node.type == 'import_as_name':
			for c in node.children:
				if c.type == 'keyword': # we just the children (usually only one) until we hit the "as" keyword
					break
				self.recordErrorsForUnsolvedImports(c)
		elif node.type == 'import_name':
			for c in node.children:
				self.recordErrorsForUnsolvedImports(c)
		elif node.type == 'dotted_as_names':
			for c in node.children:
				self.recordErrorsForUnsolvedImports(c)
		elif node.type == 'dotted_as_name':
			for c in node.children:
				if c.type == 'keyword': # we just the children (usually only one) until we hit the "as" keyword
					break
				self.recordErrorsForUnsolvedImports(c)
		elif node.type == 'dotted_name':
			for c in node.children:
				if self.recordErrorsForUnsolvedImports(c) is False:
					return False
		elif node.type == 'name':
			if len(self.getDefinitionsOfNode(node, self.sourceFilePath)) == 0:
				self.client.recordError('Imported symbol named "' + node.value + '" has not been found.', False, getSourceRangeOfNode(node))
				return False
		return True


	def recordInstanceReference(self, node, definition):
		nameHierarchy = self.getNameHierarchyFromFullNameOfDefinition(definition)
		if nameHierarchy is not None:
			referencedSymbolId = self.client.recordSymbol(nameHierarchy)
			self.client.recordSymbolKind(referencedSymbolId, srctrl.SYMBOL_GLOBAL_VARIABLE)

			referenceKind = srctrl.REFERENCE_USAGE
			if getParentWithType(node, 'import_from') is not None:
				# this would be the case for "from foo import f as my_f"
				#                                             ^    ^
				referenceKind = srctrl.REFERENCE_IMPORT

			referenceId = self.client.recordReference(
				self.contextStack[-1].id,
				referencedSymbolId,
				referenceKind
			)
			self.client.recordReferenceLocation(referenceId, getSourceRangeOfNode(node))
			return True
		return False


	def recordModuleReference(self, node, definition):
		referencedNameHierarchy = self.getNameHierarchyFromModulePathOfDefinition(definition)
		if referencedNameHierarchy is None:
			referencedNameHierarchy = self.getNameHierarchyFromFullNameOfDefinition(definition)
		if referencedNameHierarchy is None:
			return False

		referencedSymbolId = self.client.recordSymbol(referencedNameHierarchy)

		# Record symbol kind. If the used type is within indexed code, we already have this info. In any other case, this is valuable info!
		self.client.recordSymbolKind(referencedSymbolId, srctrl.SYMBOL_MODULE)

		if isQualifierNode(node):
			self.client.recordQualifierLocation(referencedSymbolId, getSourceRangeOfNode(node))
		else:
			referenceKind = srctrl.REFERENCE_USAGE
			if getParentWithType(node, 'import_name') is not None:
				# this would be the case for "import foo"
				#                                    ^
				referenceKind = srctrl.REFERENCE_IMPORT

			referenceId = self.client.recordReference(
				self.contextStack[-1].id,
				referencedSymbolId,
				referenceKind
			)

			self.client.recordReferenceLocation(referenceId, getSourceRangeOfNode(node))
		return True


	def recordClassReference(self, node, definition):
		referencedNameHierarchy = self.getNameHierarchyOfClassOrFunctionDefinition(definition)
		if referencedNameHierarchy is None:
			return False

		referencedSymbolId = self.client.recordSymbol(referencedNameHierarchy)

		# Record symbol kind. If the used type is within indexed code, we already have this info. In any other case, this is valuable info!
		self.client.recordSymbolKind(referencedSymbolId, srctrl.SYMBOL_CLASS)

		if isQualifierNode(node):
			self.client.recordQualifierLocation(referencedSymbolId, getSourceRangeOfNode(node))
		else:
			referenceKind = srctrl.REFERENCE_TYPE_USAGE
			if node.parent is not None:
				if node.parent.type == 'classdef':
					# this would be the case for "class Foo(Bar)"
					#                                       ^
					referenceKind = srctrl.REFERENCE_INHERITANCE
				elif node.parent.type in ['arglist', 'testlist'] and node.parent.parent is not None and node.parent.parent.type == 'classdef':
					# this would be the case for "class Foo(Bar, Baz)"
					#                                       ^    ^
					referenceKind = srctrl.REFERENCE_INHERITANCE
				elif getParentWithType(node, 'import_from') is not None:
					# this would be the case for "from foo import Foo as F"
					#                                             ^      ^
					referenceKind = srctrl.REFERENCE_IMPORT

			referenceId = self.client.recordReference(
				self.contextStack[-1].id,
				referencedSymbolId,
				referenceKind
			)

			self.client.recordReferenceLocation(referenceId, getSourceRangeOfNode(node))
		return True


	def recordFunctionReference(self, node, definition):
		referencedNameHierarchy = self.getNameHierarchyOfClassOrFunctionDefinition(definition)
		if referencedNameHierarchy is None:
			return False

		referencedSymbolId = self.client.recordSymbol(referencedNameHierarchy)

		# Record symbol kind. If the called function is within indexed code, we already have this info. In any other case, this is valuable info!
		self.client.recordSymbolKind(referencedSymbolId, srctrl.SYMBOL_FUNCTION)

		referenceKind = -1
		nextNode = getNext(node)
		if nextNode is not None and nextNode.type == 'trailer':
			if len(nextNode.children) >= 2 and nextNode.children[0].value == '(' and nextNode.children[-1].value == ')':
				referenceKind = srctrl.REFERENCE_CALL
		elif getParentWithType(node, 'import_from'):
			referenceKind = srctrl.REFERENCE_IMPORT

		if referenceKind is -1:
			return False

		referenceId = self.client.recordReference(
			self.contextStack[-1].id,
			referencedSymbolId,
			referenceKind
		)

		self.client.recordReferenceLocation(referenceId, getSourceRangeOfNode(node))
		return True


	def recordParamReference(self, node, definition):
		localSymbolId = self.client.recordLocalSymbol(self.getLocalSymbolName(definition))
		self.client.recordLocalSymbolLocation(localSymbolId, getSourceRangeOfNode(node))
		return True


	def recordStatementReference(self, node, definition):
		definitionModulePath = definition.module_path
		if definitionModulePath is None:
			if self.sourceFilePath == _virtualFilePath:
				definitionModulePath = self.sourceFilePath
			else:
				return False

		symbolKind = None
		referenceKind = None
		definitionKind = None

		definitionNameNode = definition._name.tree_name
		namedDefinitionParentNode = getParentWithTypeInList(definitionNameNode, ['classdef', 'funcdef'])
		if namedDefinitionParentNode is not None:
			if namedDefinitionParentNode.type in ['classdef']:
				# definition is a static member variable
				if definitionNameNode.start_pos == node.start_pos and definitionNameNode.end_pos == node.end_pos:
					# node is the definition of the static member variable
					symbolKind = srctrl.SYMBOL_FIELD
					definitionKind = srctrl.DEFINITION_EXPLICIT
				else:
					# node is a usage of the static member variable
					referenceKind = srctrl.REFERENCE_USAGE
			elif namedDefinitionParentNode.type in ['funcdef']:
				# definition may be a non-static member variable
				if definitionNameNode.parent is not None and definitionNameNode.parent.type == 'trailer':
					potentialParamNode = getNamedParentNode(definitionNameNode)
					if potentialParamNode is not None:
						for potentialParamDefinition in self.getDefinitionsOfNode(potentialParamNode, definitionModulePath):
							if potentialParamDefinition is not None and potentialParamDefinition.type == 'param':
								paramDefinitionNameNode = potentialParamDefinition._name.tree_name
								potentialFuncdefNode = getNamedParentNode(paramDefinitionNameNode)
								if potentialFuncdefNode is not None and potentialFuncdefNode.type == 'funcdef':
									potentialClassdefNode = getNamedParentNode(potentialFuncdefNode)
									if potentialClassdefNode is not None and potentialClassdefNode.type == 'classdef':
										preceedingNode = paramDefinitionNameNode.parent.get_previous_sibling()
										if preceedingNode is not None and preceedingNode.type != 'param':
											# 'paramDefinitionNameNode' is the first parameter of a member function (aka. 'self')
											referenceKind = srctrl.REFERENCE_USAGE
											if definitionNameNode.start_pos == node.start_pos and definitionNameNode.end_pos == node.end_pos:
												symbolKind = srctrl.SYMBOL_FIELD
												definitionKind = srctrl.DEFINITION_EXPLICIT
		else:
			symbolKind = srctrl.SYMBOL_GLOBAL_VARIABLE
			if definitionNameNode.start_pos == node.start_pos and definitionNameNode.end_pos == node.end_pos:
				# node is the definition of a global variable
				definitionKind = srctrl.DEFINITION_EXPLICIT
			elif getParentWithType(node, 'import_from') is not None:
				# this would be the case for "from foo import f as my_f"
				#                                             ^    ^
				referenceKind = srctrl.REFERENCE_IMPORT
			else:
				referenceKind = srctrl.REFERENCE_USAGE

		sourceRange = getSourceRangeOfNode(node)

		if symbolKind is not None or referenceKind is not None:
			symbolNameHierarchy = self.getNameHierarchyOfNode(definitionNameNode, definitionModulePath)
			if symbolNameHierarchy is None:
				return False

			symbolId = self.client.recordSymbol(symbolNameHierarchy)

			if symbolKind is not None:
				self.client.recordSymbolKind(symbolId, symbolKind)

			if definitionKind is not None:
				self.client.recordSymbolDefinitionKind(symbolId, definitionKind)
				self.client.recordSymbolLocation(symbolId, sourceRange)

			if referenceKind is not None:
				referenceId = self.client.recordReference(
					self.contextStack[-1].id,
					symbolId,
					referenceKind
				)
				self.client.recordReferenceLocation(referenceId, sourceRange)
		else:
			localSymbolId = self.client.recordLocalSymbol(self.getLocalSymbolName(definition))
			self.client.recordLocalSymbolLocation(localSymbolId, sourceRange)
		return True


	def getLocalSymbolName(self, definition):
		definitionNameNode = definition._name.tree_name

		definitionModulePath = definition.module_path
		if definitionModulePath is None:
			if self.sourceFilePath == _virtualFilePath:
				definitionModulePath = self.sourceFilePath

		contextName = ''
		if definitionModulePath is not None:
			parentFuncdef = getParentWithType(definitionNameNode, 'funcdef')
			if parentFuncdef is not None:
				parentFuncdefNameNode = getFirstDirectChildWithType(parentFuncdef, 'name')
				if parentFuncdefNameNode is not None:
					parentFuncdefNameHierarchy = self.getNameHierarchyOfNode(parentFuncdefNameNode, definitionModulePath)
					if parentFuncdefNameHierarchy is not None:
						contextName = parentFuncdefNameHierarchy.getDisplayString()

		if len(contextName) == 0:
			contextName = str(self.contextStack[-1].name)

		return contextName + '<' + definitionNameNode.value + '>'


	def getNameHierarchyFromModuleFilePath(self, filePath):
		if filePath is None:
			return None

		if filePath == _virtualFilePath:
			return NameHierarchy(NameElement(os.path.splitext(_virtualFilePath)[0]), '.')

		filePath = os.path.abspath(filePath)
		# First remove the suffix.
		for suffix in all_suffixes():
			if filePath.endswith(suffix):
				filePath = filePath[:-len(suffix)]
				break

		for p in self.sysPath:
			if filePath.startswith(p):
				rest = filePath[len(p):]
				if rest.startswith(os.path.sep):
					# Remove a slash in cases it's still there.
					rest = rest[1:]
				if rest:
					split = rest.split(os.path.sep)
					for string in split:
						if not string:
							return None

					if split[-1] == '__init__':
						split = split[:-1]

					nameHierarchy = None
					for namePart in split:
						if nameHierarchy is None:
							nameHierarchy = NameHierarchy(NameElement(namePart), '.')
						else:
							nameHierarchy.nameElements.append(NameElement(namePart))
					return nameHierarchy

		return None


	def getNameHierarchyFromModulePathOfDefinition(self, definition):
		nameHierarchy = self.getNameHierarchyFromModuleFilePath(definition.module_path)
		if nameHierarchy is not None:
			if nameHierarchy.nameElements[-1].name != definition.name:
				nameHierarchy.nameElements.append(NameElement(definition.name))
		return nameHierarchy


	def getNameHierarchyFromFullNameOfDefinition(self, definition):
		nameHierarchy = None
		for namePart in definition.full_name.split('.'):
			if nameHierarchy is None:
				nameHierarchy = NameHierarchy(NameElement(namePart), '.')
			else:
				nameHierarchy.nameElements.append(NameElement(namePart))
		return nameHierarchy


	def getNameHierarchyOfClassOrFunctionDefinition(self, definition):
		if definition is None:
			return None

		if definition.line is None and definition.column is None:
			if definition.module_name in ['builtins', '__builtin__']:
				nameHierarchy = NameHierarchy(NameElement('builtin'), '.')
				for namePart in definition.full_name.split('.'):
					nameHierarchy.nameElements.append(NameElement(namePart))
				return nameHierarchy
			else:
				return self.getNameHierarchyFromFullNameOfDefinition(definition)

		else:
			if definition._name is None or definition._name.tree_name is None:
				return None

			definitionModulePath = definition.module_path
			if definitionModulePath is None:
				if self.sourceFilePath == _virtualFilePath:
					definitionModulePath = self.sourceFilePath
				else:
					return None

			return self.getNameHierarchyOfNode(definition._name.tree_name, definitionModulePath)


	def getDefinitionsOfNode(self, node, nodeSourceFilePath):
		try:
			(startLine, startColumn) = node.start_pos
			if nodeSourceFilePath == _virtualFilePath: # we are indexing a provided code snippet
				script = jedi.Script(
					source = self.sourceFileContent,
					line = startLine,
					column = startColumn,
					environment = self.environment,
					sys_path = self.sysPath
				)
			else: # we are indexing a real file
				script = jedi.Script(
					source = None,
					line = startLine,
					column = startColumn,
					path = nodeSourceFilePath,
					environment = self.environment,
					sys_path = self.sysPath
				)
			return script.goto_assignments(follow_imports=True)

		except Exception:
			return []


	def getNameHierarchyOfNode(self, node, nodeSourceFilePath):
		if node is None:
			return None

		if node.type == 'name':
			nameNode = node
		else:
			nameNode = getFirstDirectChildWithType(node, 'name')

		if nameNode is None:
			return None

		# we derive the name for the canonical node (e.g. the node's definition)
		for definition in self.getDefinitionsOfNode(nameNode, nodeSourceFilePath):
			if definition is None:
				continue

			definitionModulePath = definition.module_path
			if definitionModulePath is None:
				if self.sourceFilePath == _virtualFilePath:
					definitionModulePath = self.sourceFilePath
				else:
					continue

			definitionNameNode = definition._name.tree_name
			if definitionNameNode is None:
				continue

			parentNode = getNamedParentNode(definitionNameNode)

			# if the node is defines as a non-static member variable, we remove the "function_name.self" from the
			# name hierarchy (e.g. "Foo.__init__.self.bar" gets shortened to "Foo.bar")
			if parentNode is not None:
				parentNameNode = getFirstDirectChildWithType(parentNode, 'name')
				if parentNameNode is not None:
					for parentDefinition in self.getDefinitionsOfNode(parentNameNode, definitionModulePath):
						if parentDefinition is None or parentDefinition.type != 'param':
							continue

						parentDefinitionNameNode = parentDefinition._name.tree_name

						potentialFuncdefNode = getNamedParentNode(parentDefinitionNameNode)
						if potentialFuncdefNode is None or potentialFuncdefNode.type != 'funcdef':
							continue

						potentialClassdefNode = getNamedParentNode(potentialFuncdefNode)
						if potentialClassdefNode is None or potentialClassdefNode.type != 'classdef':
							continue

						preceedingNode = parentDefinitionNameNode.parent.get_previous_sibling()
						if preceedingNode is not None and preceedingNode.type != 'param':
							# 'node' is the first parameter of a member function (aka. 'self')
							parentNode =  potentialClassdefNode

			nameElement = NameElement(definitionNameNode.value)

			if parentNode is not None:
				parentNodeNameHierarchy = self.getNameHierarchyOfNode(parentNode, definitionModulePath)
				if parentNodeNameHierarchy is None:
					parentNodeNameHierarchy = getNameHierarchyForUnsolvedSymbol()
				parentNodeNameHierarchy.nameElements.append(nameElement)
				return parentNodeNameHierarchy

			nameHierarchy = self.getNameHierarchyFromModuleFilePath(nodeSourceFilePath)
			if nameHierarchy is None:
				nameHierarchy = getNameHierarchyForUnsolvedSymbol()
			nameHierarchy.nameElements.append(nameElement)
			return nameHierarchy

		return None


class VerboseAstVisitor(AstVisitor):

	def __init__(self, client, evaluator, sourceFilePath, sourceFileContent = None, sysPath = None):
		AstVisitor.__init__(self, client, evaluator, sourceFilePath, sourceFileContent, sysPath)
		self.indentationLevel = 0
		self.indentationToken = '| '


	def traverseNode(self, node):
		currentString = ''
		for i in range(0, self.indentationLevel):
			currentString += self.indentationToken

		currentString += node.type

		if hasattr(node, 'value'):
			currentString += ' (' + repr(node.value) + ')'

		currentString += ' ' + getSourceRangeOfNode(node).toString()

		print('AST: ' + currentString)

		self.indentationLevel += 1
		AstVisitor.traverseNode(self, node)
		self.indentationLevel -= 1


class AstVisitorClient:

	def __init__(self):
		self.indexedFileId = 0
		if srctrl.isCompatible():
			print('INFO: Loaded database is compatible.')
		else:
			print('WARNING: Loaded database is not compatible.')
			print('INFO: Supported DB Version: ' + str(srctrl.getSupportedDatabaseVersion()))
			print('INFO: Loaded DB Version: ' + str(srctrl.getLoadedDatabaseVersion()))


	def recordSymbol(self, nameHierarchy):
		if nameHierarchy is not None:
			symbolId = srctrl.recordSymbol(nameHierarchy.serialize())
			return symbolId
		return 0


	def recordSymbolDefinitionKind(self, symbolId, symbolDefinitionKind):
		srctrl.recordSymbolDefinitionKind(symbolId, symbolDefinitionKind)


	def recordSymbolKind(self, symbolId, symbolKind):
		srctrl.recordSymbolKind(symbolId, symbolKind)


	def recordSymbolLocation(self, symbolId, sourceRange):
		srctrl.recordSymbolLocation(
			symbolId,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordSymbolScopeLocation(self, symbolId, sourceRange):
		srctrl.recordSymbolScopeLocation(
			symbolId,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordSymbolSignatureLocation(self, symbolId, sourceRange):
		srctrl.recordSymbolSignatureLocation(
			symbolId,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordReference(self, contextSymbolId, referencedSymbolId, referenceKind):
		return srctrl.recordReference(
			contextSymbolId,
			referencedSymbolId,
			referenceKind
		)


	def recordReferenceLocation(self, referenceId, sourceRange):
		srctrl.recordReferenceLocation(
			referenceId,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordQualifierLocation(self, referencedSymbolId, sourceRange):
		return srctrl.recordQualifierLocation(
			referencedSymbolId,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordFile(self, filePath):
		self.indexedFileId = srctrl.recordFile(filePath.replace('\\', '/'))
		srctrl.recordFileLanguage(self.indexedFileId, 'python')
		return self.indexedFileId


	def recordFileLanguage(self, fileId, languageIdentifier):
		srctrl.recordFileLanguage(fileId, languageIdentifier)


	def recordLocalSymbol(self, name):
		return srctrl.recordLocalSymbol(name)


	def recordLocalSymbolLocation(self, localSymbolId, sourceRange):
		srctrl.recordLocalSymbolLocation(
			localSymbolId,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordAtomicSourceRange(self, sourceRange):
		srctrl.recordAtomicSourceRange(
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


	def recordError(self, message, fatal, sourceRange):
		srctrl.recordError(
			message,
			fatal,
			self.indexedFileId,
			sourceRange.startLine,
			sourceRange.startColumn,
			sourceRange.endLine,
			sourceRange.endColumn
		)


class SourceRange:

	def __init__(self, startLine, startColumn, endLine, endColumn):
		self.startLine = startLine
		self.startColumn = startColumn
		self.endLine = endLine
		self.endColumn = endColumn


	def toString(self):
		return '[' + str(self.startLine) + ':' + str(self.startColumn) + '|' + str(self.endLine) + ':' + str(self.endColumn) + ']'


class NameHierarchy():

	unsolvedSymbolName = 'unsolved symbol' # this name should not collide with normal symbol name, because they cannot contain space characters

	def __init__(self, nameElement, delimiter):
		self.nameElements = []
		if nameElement is not None:
			self.nameElements.append(nameElement)
		self.delimiter = delimiter


	def serialize(self):
		return json.dumps(self, cls=NameHierarchyEncoder)


	def getDisplayString(self):
		displayString = ''
		isFirst = True
		for nameElement in self.nameElements:
			if not isFirst:
				displayString += self.delimiter
			isFirst = False
			if len(nameElement.prefix) > 0:
				displayString += nameElement.prefix + ' '
			displayString += nameElement.name
			if len(nameElement.postfix) > 0:
				displayString += nameElement.postfix
		return displayString


class NameElement:

	def __init__(self, name, prefix = '', postfix = ''):
		self.name = name
		self.prefix = prefix
		self.postfix = postfix


class NameHierarchyEncoder(json.JSONEncoder):

	def default(self, obj):
		if isinstance(obj, NameHierarchy):
			return {
				'name_delimiter': obj.delimiter,
				'name_elements': [nameElement.__dict__ for nameElement in obj.nameElements]
			}
		# Let the base class default method raise the TypeError
		return json.JSONEncoder.default(self, obj)


def getNameHierarchyForUnsolvedSymbol():
	return NameHierarchy(NameElement(NameHierarchy.unsolvedSymbolName), '.')


def isQualifierNode(node):
	nextNode = getNext(node)
	if nextNode is not None and nextNode.type == 'trailer':
		nextNode = getNext(nextNode)
	if nextNode is not None and nextNode.type == 'operator' and nextNode.value == '.':
		return True
	return False


def getSourceRangeOfNode(node):
	startLine, startColumn = node.start_pos
	endLine, endColumn = node.end_pos
	return SourceRange(startLine, startColumn + 1, endLine, endColumn)


def getNamedParentNode(node):
	if node is None:
		return None

	parentNode = node.parent

	if node.type == 'name' and parentNode is not None:
		parentNode = parentNode.parent

	while parentNode is not None:
		if getFirstDirectChildWithType(parentNode, 'name') is not None:
			return parentNode
		parentNode = parentNode.parent

	return None


def getParentWithType(node, type):
	if node == None:
		return None
	parentNode = node.parent
	if parentNode == None:
		return None
	if parentNode.type == type:
		return parentNode
	return getParentWithType(parentNode, type)


def getParentWithTypeInList(node, typeList):
	if node == None:
		return None
	parentNode = node.parent
	if parentNode == None:
		return None
	if parentNode.type in typeList:
		return parentNode
	return getParentWithTypeInList(parentNode, typeList)


def getFirstDirectChildWithType(node, type):
	for c in node.children:
		if c.type == type:
			return c
	return None


def getDirectChildrenWithType(node, type):
	children = []
	for c in node.children:
		if c.type == type:
			children.append(c)
	return children


def getNext(node):
	if hasattr(node, 'children'):
		for c in node.children:
			return c

	siblingSource = node
	while siblingSource is not None and siblingSource.parent is not None:
		sibling = siblingSource.get_next_sibling()
		if sibling is not None:
			return sibling
		siblingSource = siblingSource.parent

	return None

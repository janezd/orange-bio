from go import *

from urllib import urlretrieve
from gzip import GzipFile
import tarfile
import shutil
from datetime import datetime

try:
    import orngServerFiles
    default_database_path = os.path.join(orngServerFiles.localpath(), "GO")
except Exception:
    default_database_path = os.curdir

_verbose = False

def verbose(f):
    def func(*args, **kwargs):
        if _verbose:
            print "Starting", f.__name__
            start = datetime.now()
        ret = f(*args, **kwargs)
        if _verbose:
            print f.__name__, "computed in %i seconds" % (datetime.now() - start).seconds
        return ret
    return func

builtinOBOObjects = ["""
[Typedef]
id: is_a
name: is_a
range: OBO:TERM_OR_TYPE
domain: OBO:TERM_OR_TYPE
definition: The basic subclassing relationship [OBO:defs]"""
,
"""[Typedef]
id: disjoint_from
name: disjoint_from
range: OBO:TERM
domain: OBO:TERM
definition: Indicates that two classes are disjoint [OBO:defs]"""
,
"""[Typedef]
id: instance_of
name: instance_of
range: OBO:TERM
domain: OBO:INSTANCE
definition: Indicates the type of an instance [OBO:defs]"""
,
"""[Typedef]
id: inverse_of
name: inverse_of
range: OBO:TYPE
domain: OBO:TYPE
definition: Indicates that one relationship type is the inverse of another [OBO:defs]"""
,
"""[Typedef]
id: union_of
name: union_of
range: OBO:TERM
domain: OBO:TERM
definition: Indicates that a term is the union of several others [OBO:defs]"""
,
"""[Typedef]
id: intersection_of
name: intersection_of
range: OBO:TERM
domain: OBO:TERM
definition: Indicates that a term is the intersection of several others [OBO:defs]"""]

class OBOObject(object):
    def __init__(self, ontology=None, stanza=None):
        self.ontology = ontology
        self._lines = []
        self.values = {}
        self.related = set()
        self.relatedTo = set()
        if stanza:
            self.ParseStanza(stanza)

    def ParseStanza(self, stanza):
        for line in stanza.split("\n"):
            if ":" not in line:
                continue
            tag, rest = line.split(":", 1)
            value, modifiers, comment = "", "", ""
            if "!" in rest:
                rest, comment = rest.split("!")
            if "{" in rest:
                value, modifiers = rest.split("{", 1)
                modifiers = modifiers.strip("}")
            else:
                value = rest
            value = value.strip()
            self._lines.append((tag, value, modifiers, comment))
            if tag in multipleTagSet:
                self.values[tag] = self.values.get(tag, []) + [value]
            else:
                self.values[tag] = value
        self.related = set(self.GetRelatedObjects())

    def GetRelatedObjects(self):
        result = [(typeId, id) for typeId in ["is_a"] for id in self.values.get(typeId, [])] ##TODO add other builtin Typedef ids
        result = result + [tuple(r.split(None, 1)) for r in self.values.get("relationship", [])]
        return result

    def __repr__(self):
        repr = "[%s]\n" % type(self).__name__
        for tag, value, modifiers, comment in self._lines:
            repr = repr + tag + ": " + value
            if modifiers:
                repr = repr + "{ " + modifiers + " }"
            if comment:
                repr = repr + " ! " + comment
            repr = repr + "\n"
        return repr

    def __str__(self):
        return self.id

    def __getattr__(self, name):
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name)
        
class Term(OBOObject):
    pass

class Typedef(OBOObject):
    pass

class Instance(OBOObject):
    pass
        
class Ontology(object):
    def __init__(self, file=None, progressCallback=None):
        self.terms = {}
        self.typedefs = {}
        self.instances = {}
        if file:
            self.ParseFile(file, progressCallback)

    @classmethod
    def Load(cls, progressCallback=None):
        """A class method that tries to load the ontology file from default_database_path.
        """
        files = [name for name in os.listdir(default_database_path) if name.startswith("gene_ontology")]
        try:
            return cls(os.path.join(default_database_path, files.pop()), progressCallback=progressCallback)
        except Exception:
            raise IOError("Could not locate ontology file in " + default_database_path)
        
    def ParseFile(self, file, progressCallback=None):
        if type(file) == str:
            f = tarfile.open(file).extractfile("gene_ontology_edit.obo") if tarfile.is_tarfile(file) else open(file)
        else:
            f = file
        data = f.readlines()
        data = "".join([line for line in data if not line.startswith("!")])
        c=re.compile("\[.+?\].*?\n\n", re.DOTALL)
##        print "re find"
        data=c.findall(data)
##        print "end re find"
##        print len(data)
        for i, block in enumerate(builtinOBOObjects + data):
            if block.startswith("[Term]"):
                term = Term(self, block)
                self.terms[term.id] = term
            elif block.startswith("[Typedef]"):
                typedef = Typedef(self, block)
                self.typedefs[typedef.id] = typedef
            elif block.startswith("[Instance]"):
                instance = Instance(self, block)
                self.instances[instance.id] = instance
            if progressCallback:
                progressCallback(100.0*i/len(data))
        
        self.aliasMapper = {}
        for id, term in self.terms.items():
            for typeId, parent in term.related:
                self.terms[parent].relatedTo.add((typeId, id))

    def ExtractSuperGraph(self, terms):
        """Return all super terms of terms up to the most general one.
        """
        visited = set()
        queue = set(terms)
        while queue:
            term = queue.pop()
            visited.add(term)
            queue.update(set(id for typeId, id in self.terms[term].related) - visited)
        return visited

    def ExtractSubGraph(self, terms):
        """Return all sub terms of terms.
        """
        visited = set()
        queue = set(terms)
        while queue:
            term = queue.pop()
            visited.add(term)
            queue.update(set(id for typeId, id in self.terms[term].relatedTo) - visited)
        return visited

    def GetTermDepth(self, term, cache_={}):
        if term not in cache:
            cache[term] = min([self.GetTermDepth(parent) + 1 for typeId, parent in self.terms[term].related] or [1])
        return cache[term]

    def __getitem__(self, name):
        return self.terms.__getitem__(name)

    @staticmethod
    def DownloadOntology(file, progressCallback=None):
        tFile = tarfile.open(file, "w:gz") if type(file) == str else file
        tmpDir = os.path.join(orngEnviron.bufferDir, "tmp_go/")
        try:
            os.mkdir(tmpDir)
        except Exception:
            pass
        urlretrieve("http://www.geneontology.org/ontology/gene_ontology_edit.obo", os.path.join(tmpDir, "gene_ontology_edit.obo"), progressCallback and __progressCallbackWrapper(progressCallback))
        tFile.add(os.path.join(tmpDir, "gene_ontology_edit.obo"), "gene_ontology_edit.obo")
        try:
            shutil.rmtree(tmpDir)
        except Exception:
            pass

class Annotations(object):
    def __init__(self, file=None, ontology=None, progressCallback=None):
        self.file = file
        self.ontology = ontology
        self.allAnnotations = defaultdict(list)
        self.geneAnnotations = defaultdict(list)
        self.termAnnotations = defaultdict(list)
        self.geneNames = set()
        self.geneNamesDict = {}
        self.aliasMapper = {}
        self.additionalAliases = {}
        self.annotations = []
        self.annotationsById = {}
        self.header = ""
        self.geneMapper = None
        if file:
            self.ParseFile(file, progressCallback)

    @classmethod
    def Load(cls, org):
        """A class method that tries to load the association file for the given organism from default_database_path.
        """
        files = [name for name in os.listdir(default_database_path) if name.startswith("gene_association") and org in name]
        try:
            return cls(os.path.join(default_database_path, files.pop()))
        except Exception:
            raise IOError("Could not locate gene association file in " + default_database_path)
    
    def ParseFile(self, file, progressCallback=None):
        if type(file) == str:
            f = tarfile.open(file).extractfile("gene_association") if tarfile.is_tarfile(file) else open(file)
        else:
            f = file
        lines = f.readlines()
        milestones = set(i for i in range(0, len(lines), max(len(lines)/100, 1)))
        for i,line in enumerate(lines):
            if line.startswith("!"):
                self.header = self.header + line + "\n"
                continue
            a=Annotation(line)
            self.annotationsById[id(a)] = a
            if not a.geneName or not a.GOId:
                continue
            if a.geneName not in self.geneNames:
                self.geneNames.add(a.geneName)
                self.geneAnnotations[a.geneName].append(a)
                for alias in a.alias:
                    self.aliasMapper[alias] = a.geneName
                for alias in a.aditionalAliases:
                    self.additionalAliases[alias] = a.geneName
                self.aliasMapper[a.geneName] = a.geneName
                self.aliasMapper[a.DB_Object_ID] = a.geneName
                names = [a.DB_Object_ID, a.DB_Object_Symbol]
                names.extend(a.alias)
                for n in names:
                    self.geneNamesDict[n] = names
            else:
                self.geneAnnotations[a.geneName].append(a)
            self.annotations.append(a)
            self.termAnnotations[a.GOId].append(a)
            if progressCallback and i in milestones:
                progressCallback(100.0*i/len(lines))

    def GetGeneNamesTranslator(self, genes):
        def alias(gene):
            return self.aliasMapper.get(gene, self.additionalAliases.get(gene, None))
        return dict([(alias(gene), gene) for gene in genes if alias(gene)])

    def _CollectAnnotations(self, id):
        if id not in self.allAnnotations:
            annotations = [self.termAnnotations[id]]
            for typeId, child in self.ontology[id].relatedTo:
                aa = self._CollectAnnotations(child)
                if type(aa) == set: ## if it was allready reduced in GetAllAnnotations
                    annotations.append(aa)
                else:
                    annotations.extend(aa)
            self.allAnnotations[id] = annotations
        return self.allAnnotations[id]

    def GetAllAnnotations(self, id):
        """Return a set of all annotations for this and all subterms.
        """
        if id not in self.allAnnotations or type(self.allAnnotations[id]) == list:
            annot_set = set()
            for annots in self._CollectAnnotations(id):
                annot_set.update(annots)
            self.allAnnotations[id] = annot_set
        return self.allAnnotations[id]


    def GetAllGenes(self, id, evidenceCodes = None):
        """Return a list of genes annotated by specified evidence codes to this and all subterms."
        """
        evidenceCodes = set(evidenceCodes or evidenceDict.keys())
        annotations = self.GetAllAnnotations(id)
        return list(set([ann.geneName for ann in annotations if ann.Evidence_code in evidenceCodes]))

    def GetEnrichedTerms(self, genes, reference=None, evidenceCodes=None, slimsOnly=False, aspect="P", progressCallback=None):
        """Return a dictionary of enriched terms, with tuples of (list_of_genes, p_value, reference_count)
        """
        revGenesDict = self.GetGeneNamesTranslator(genes)
        genes = set(revGenesDict.keys())
        reference = set(reference) if reference else self.geneNames
        evidenceCodes = set(evidenceCodes or evidenceDict.keys())
        annotations = [ann for gene in genes for ann in self.geneAnnotations[gene] if ann.Evidence_code in evidenceCodes and ann.Aspect == aspect]
        refAnnotations = set([ann for gene in reference for ann in self.geneAnnotations[gene] if ann.Evidence_code in evidenceCodes and ann.Aspect == aspect])
        annotationsDict = defaultdict(set)
        for ann in annotations:
            annotationsDict[ann.GO_ID].add(ann)
##        allGenes = set(ann.geneName for ann in annotations)
##        allRefGenes = set(ann.geneName for ann in refAnnotations)
        terms = self.ontology.ExtractSuperGraph(annotationsDict.keys())
        res = {}
        score = obiProb.Binomial()
        milestones = set(range(0, len(terms), max(len(terms)/100, 1)))
        for i, term in enumerate(terms):
            allAnnotations = self.GetAllAnnotations(term)
            allAnnotations.intersection_update(refAnnotations)
            allAnnotatedGenes = set([ann.geneName for ann in allAnnotations]) #if ann.Aspect == aspect and ann.Evidence_code in evidenceCodes])
##            mappedGenes = set(ann.geneName for ann in allAnnotations.intersection(annotations))
            if len(genes) > len(allAnnotatedGenes): 
                mappedGenes = genes.intersection(allAnnotatedGenes)
            else:
                mappedGenes = allAnnotatedGenes.intersection(genes)
##            mappedReferenceGenes = set(ann.geneName for ann in allAnnotations.intersection(refAnnotations))
            if len(reference) > len(allAnnotatedGenes):
                mappedReferenceGenes = reference.intersection(allAnnotatedGenes)
            else:
                mappedReferenceGenes = allAnnotatedGenes.intersection(reference)
            res[term] = ([revGenesDict[g] for g in mappedGenes], score.p_value(len(mappedGenes), len(reference), len(mappedReferenceGenes), len(genes)), len(mappedReferenceGenes))
            if progressCallback and i in milestones:
                progressCallback(100.0 * i / len(terms))
        return res

    def GetAnnotatedTerms(self, genes, directAnnotationOnly=False, evidenceCodes=None, progressCallback=None):
        """Return all terms that are annotated by with evidenceCodes.
        """
        revGenesDict = self.GetGeneNamesTranslator(genes)
        genes = set(revGenesDict.keys)
        evidenceCodes = set(evidenceCodes or evidenceDict.keys())
        annotations = [ann for gene in genes for ann in self.geneAnnotations[gene] if ann.Evidence_code in evidenceCodes]
        dd = defaultdict(set)
        for ann in annotations:
            dd[ann.GO_ID].add(ann.geneName)
        if not directAnnotationsOnly:
            terms = self.ontology.ExtractSuperGraph(dd.keys())
            for i, term in enumerate(terms):
                termAnnots = self.GetAllAnnotations(term)
                termAnnots.intersection_update(annotations)
                dd[term].update([revGenesDict.get(ann.geneName, ann.geneName) for ann in termAnots])
        return dict[d]
    
    @staticmethod
    def DownloadAnnotations(org, file, progressCallback=None):
        tFile = tarfile.open(file, "w:gz") if type(file) == str else file
        tmpDir = os.path.join(orngEnviron.bufferDir, "tmp_go/")
        try:
            os.mkdir(tmpDir)
        except Exception:
            pass
        fileName = "gene_association." + org + ".gz"
        urlretrieve("http://www.geneontology.org/gene-associations/" + fileName, os.path.join(tmpDir, fileName), progressCallback and __progressCallbackWraper(progressCallback))
        gzFile = GzipFile(os.path.join(tmpDir, fileName), "r")
        file = open(os.path.join(tmpDir, "gene_association." + org), "w")
        file.writelines(gzFile.readlines())
        file.flush()
        file.close()
##        tFile = tarfile.open(os.path.join(tmpDir, "gene_association." + org + ".tar.gz"), "w:gz")
        tFile.add(os.path.join(tmpDir, "gene_association." + org), "gene_association")
        annotation = Annotations(os.path.join(tmpDir, "gene_association." + org), progressCallback=progressCallback)
        cPickle.dump(annotation.geneNames, open(os.path.join(tmpDir, "gene_names.pickle"), "w"))
        tFile.add(os.path.join(tmpDir, "gene_names.pickle"), "gene_names.pickle")
        tFile.close()
        
        try:
            shutil.rmtree(tmpDir)
        except Exception:
            pass

class __progressCallbackWrapper:
    def __init__(self, callback):
        self.callback = callback
    def __call__(self, bCount, bSize, fSize):
        fSize = 10000000 if fSize == -1 else fSize
        self.callback(100*bCount*bSize/fSize)
        
from obiGenomicsUpdate import Update as UpdateBase
from obiGenomicsUpdate import PKGManager as PKGManagerBase

import urllib2

class Update(UpdateBase):
    def __init__(self, local_database_path=None, progressCallback=None):
        UpdateBase.__init__(self, local_database_path or getDataDir(), progressCallback)
    def CheckModified(self, addr, date=None):
        return date > self.GetLastModified(addr)
        
    def CheckModifiedOrg(self, org):
        return self.CheckModified("http://www.geneontology.org/gene-associations/gene_association." + org + ".gz", self.LastModifiedOrg(org))
    
    def LastModifiedOrg(self, org):
        return self.shelve.get((Update.UpdateAnnotation, (org,)), None)

    def GetLastModified(self, addr):
        stream = urllib2.urlopen(addr)
        return datetime.strptime(stream.headers.get("Last-Modified"), "%a, %d %b %Y %H:%M:%S %Z")
##        return stream.headers.get("Last-Modified")

    def GetAvailableOrganisms(self):
        source = urllib2.urlopen("http://www.geneontology.org/gene-associations/").read()
        return [s.split(".")[1] for s in sorted(set(re.findall("gene_association\.[a-zA-z0-9_]+?\.gz", source)))]

    def GetDownloadedOrganisms(self):
        return [name.split(".")[1] for name in os.listdir(self.local_database_path) if name.startswith("gene_association")]

    def IsUpdatable(self, func, args):
        if func == Update.UpdateOntology:
            return self.CheckModified("http://www.geneontology.org/ontology/gene_ontology.obo", self.shelve.get((Update.UpdateOntology, ()), None))
        elif func == Update.UpdateAnnotation:
            return self.CheckModifiedOrg(args[0])
            
    def GetDownloadable(self):
        orgs = set(self.GetAvailableOrganisms()) - set(self.GetDownloadedOrganisms())
        ret = []
        if (Update.UpdateOntology, ()) not in self.shelve:
            ret.append((Update.UpdateOntology, ()))
        if orgs:
            ret.extend([(Update.UpdateAnnotation, (org,)) for org in orgs])
        return ret

    def UpdateAnnotation(self, org):
##        downloadAnnotationTo(org, os.path.join(self.local_database_path, "gene_association." + org), self.progressCallback)
        Annotations.DownloadAnnotations(org, os.path.join(self.local_database_path, "gene_associations." + org + ".tar.gz"), self.progressCallback)
        self._update(Update.UpdateAnnotation, (org,), self.GetLastModified("http://www.geneontology.org/gene-associations/gene_association." + org + ".gz"))

    def UpdateOntology(self):
        Ontology.DownloadOntology(os.path.join(self.local_database_path, "gene_ontology_edit.obo.tar.gz"), self.progressCallback)
        self._update(Update.UpdateOntology, (), self.GetLastModified("http://www.geneontology.org/ontology/gene_ontology.obo"))

def _test1():
##    Ontology.DownloadOntology("ontology_arch.tar.gz")
##    Annotations.DownloadAnnotations("sgd", "annotations_arch.tar.gz")
    global _verbose
    _verbose = True
    def _print(f):
        print f
    o = Ontology("ontology_arch.tar.gz")
    a = Annotations("annotations_arch.tar.gz", ontology=o)
    import go
    loadAnnotation("sgd")
    loadGO()
##    print a.GetAllGenes("GO:0008150")
    import profile
    a.GetEnrichedTerms(sorted(a.geneNames)[:100])#, progressCallback=_print)
##    profile.runctx("a.GetEnrichedTerms(sorted(a.geneNames)[:100])", {"a":a}, {})
    a.GetEnrichedTerms(sorted(a.geneNames)[:100])#, progressCallback=_print)
    d1 = a.GetEnrichedTerms(sorted(a.geneNames)[:1000])#, progressCallback=_print)
    d2 = verbose(GOTermFinder)(sorted(a.geneNames)[:1000])
    print set(d2.keys()) - set(d1.keys())
    print set(d1.keys()) - set(d2.keys())
##    print a.GetEnrichedTerms(sorted(a.geneNames)[:100])#, progressCallback=_print)
    
if __name__ == "__main__":
    _test1()

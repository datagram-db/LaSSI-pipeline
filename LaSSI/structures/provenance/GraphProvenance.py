from typing import List

from LaSSI.external_services.Services import Services
from LaSSI.ner.AssignTypeToSingleton import AssignTypeToSingleton
from LaSSI.structures.internal_graph.Graph import Graph
from LaSSI.structures.kernels.Sentence import Sentence


class GraphProvenance:
    def __init__(self, gsm_json_graph, meu_db, is_simplistic_rewriting):
        self._internal_graph = None
        self._sentence = None
        self._nodes = None
        self._edges = None
        self.gsm_json_graph = gsm_json_graph
        self.is_simplistic_rewriting = is_simplistic_rewriting
        self.meu_db_row = meu_db
        self.atts_global = AssignTypeToSingleton()
        self.services = Services.getInstance()
        self.parmenides = self.services.getParmenides()
        self.existentials = self.services.getExistentials()

    def internal_graph(self) -> Graph:
        # Phase 0-4
        self.atts_global.groupGraphNodes(self.gsm_json_graph, self.is_simplistic_rewriting, self.parmenides,
                                         self.meu_db_row)
        # Phase 5
        self.atts_global.checkForNegation(self.gsm_json_graph)

        # Parmenides Information
        rejected_edges = self.parmenides.getRejectedVerbs()
        non_verbs = self.parmenides.getNonVerbs()

        # Now, create the internal graph from now created edges
        self._internal_graph = self.atts_global.constructIntermediateGraph(self.gsm_json_graph, rejected_edges,
                                                                           non_verbs)
        return self._internal_graph

    def sentence(self) -> List[Sentence]:
        transitive_verbs = self.parmenides.getTransitiveVerbs()
        self._sentence = self.atts_global.constructSentence(transitive_verbs)
        return self._sentence

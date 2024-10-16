__author__ = "Oliver R. Fox, Giacomo Bergami"
__copyright__ = "Copyright 2024, Oliver R. Fox, Giacomo Bergami"
__credits__ = ["Oliver R. Fox"]
__license__ = "GPL"
__version__ = "2.0"
__maintainer__ = "Oliver R. Fox, Giacomo Bergami"
__status__ = "Production"

import json
import re
from collections import defaultdict
from copy import copy
from itertools import repeat
from typing import List

from LaSSI.Parmenides.paremenides import Parmenides
from LaSSI.external_services.Services import Services
from LaSSI.files.JSONDump import EnhancedJSONEncoder
from LaSSI.structures.internal_graph.EntityRelationship import Singleton, Grouping, SetOfSingletons, Relationship
from LaSSI.structures.internal_graph.Graph import Graph
from LaSSI.structures.kernels.Sentence import Sentence, get_node_type
from LaSSI.structures.meuDB.meuDB import MeuDB


class AssignTypeToSingleton:  # TODO: This is doing a bit more than "AssignTypeToSingleton", should move some functions into different classes?
    def __init__(self, negations=None):
        if negations is None:
            negations = {'not', 'no'}
        self.negations = negations
        self.associations = set()
        self.meu_entities = defaultdict(set)
        self.node_id_map = dict()
        self.nodes = dict()
        self.edges = None
        self.services = Services.getInstance()
        self.existentials = self.services.getExistentials()

    def get_gsm_item_from_id(self, gsm_json, gsm_id):
        gsm_id_json = {row['id']: row for row in gsm_json}  # Associate ID to key value
        return gsm_id_json[gsm_id]

    def get_node_id(self, node_id):
        value = self.node_id_map.get(node_id, node_id)
        if value is not None and value in self.node_id_map.keys():  # Check if value exists and is a key
            return self.node_id_map.get(value)
        else:
            return value

    def resolve_node_id(self, node_id):
        if node_id is None:
            # TODO: Is this an incorrect assumption, that if no node is found it is a variable/existential?
            return Singleton(
                        id=-1, # TODO: Should this be a "freshID"?
                        named_entity="?" + str(self.existentials.increaseAndGetExistential()),
                        properties=frozenset(),
                        min=-1,
                        max=-1,
                        type='existential',
                        confidence=1
                    )
        else:
            return self.nodes[self.get_node_id(node_id)]

    def clear(self):
        self.associations.clear()
        self.meu_entities.clear()
        self.nodes.clear()

    def get_current_state_nodes(self):
        return self.nodes

    def does_string_have_negations(self, edge_label_name):
        return bool(re.search(r"\b(" + "|".join(re.escape(neg) for neg in self.negations) + r")\b", edge_label_name))

    # def create_merged_node(self, node):
    #     sorted_entities = sorted(node.entities, key=lambda x: float(dict(x.properties)['pos']))
    #     sorted_entity_names = list(map(getattr, sorted_entities, repeat('named_entity')))
    #     new_properties = {
    #         "specification": str(dict(sorted_entities[0].properties)['specification']),  # TODO: ?
    #         "begin": str(sorted_entities[0].min),
    #         "end": str(sorted_entities[len(sorted_entities) - 1].max),
    #         "pos": str(dict(sorted_entities[0].properties)['pos']),
    #         "number": str(dict(sorted_entities[0].properties)['number']),  # TODO: ?
    #     }
    #     merged_node = Singleton(
    #         id=node.id,
    #         named_entity=' '.join(sorted_entity_names),
    #         properties=frozenset(new_properties.items()),
    #         min=sorted_entities[0].min,
    #         max=sorted_entities[len(sorted_entity_names) - 1].max,
    #         type='ENTITY',
    #         confidence=1
    #     )
    #     return merged_node
    # def mergePossibleSharedSetOfSingletons(self):
    #     allSetOfSingletons = []
    #     for item in self.nodes:
    #         if isinstance(item, SetOfSingletons):
    #             allSetOfSingletons.append(item)

    def freshId(self):
        v = self.max_id
        self.max_id += 1
        return v

    def groupGraphNodes(self, gsm_json, is_simplistic_rewriting, parmenides, meu_db_row):
        # Used for when we want to generate a "fresh ID"
        self.max_id = max(map(lambda x: int(x["id"]), gsm_json)) + 1

        # Phase 0
        self.preProcessing(gsm_json)

        # Phase 1
        self.extractLogicalConnectorsAsGroups(parmenides, gsm_json, is_simplistic_rewriting)

        # Phase 1.5
        # self.mergePossibleSharedSetOfSingletons()

        # Phase 2
        for key in self.nodes:
            self.associateNodeToMeuMatches(self.nodes[key], meu_db_row)

        # Phase 3
        self.singletonTypeResolution()

        # Phase 4
        self.resolveGraphNERs(is_simplistic_rewriting, parmenides, meu_db_row)

    # Phase 0
    def preProcessing(self, gsm_json):
        # Pre-processing not semantically driven
        # Scan for 'inherit' edges and contain them in the node that has that edge
        number_of_nodes = range(len(gsm_json))
        for row in number_of_nodes:
            gsm_item = gsm_json[row]
            edges_to_keep = []
            for edge in gsm_item['phi']:
                if 'inherit_' in edge['containment']:
                    node_to_inherit = self.get_gsm_item_from_id(gsm_json, edge['score']['child'])
                    if edge['containment'].endswith('_edge'):
                        gsm_item['xi'] = node_to_inherit['xi']
                        gsm_item['ell'] = node_to_inherit['ell']
                        gsm_item['properties'] = node_to_inherit['properties']

                    for edge_to_inherit in node_to_inherit['phi']:
                        edge_to_inherit['score']['parent'] = gsm_item['id']
                        gsm_item['phi'].append(dict(edge_to_inherit))

                    # Remove edges from node that has been inherited
                    node_to_inherit['phi'] = []
                else:
                    edges_to_keep.append(edge)

            # Ignore 'inherit_edge' as they are accounted for, keep all other edges
            gsm_item['phi'] = edges_to_keep

    # Phase 1
    def extractLogicalConnectorsAsGroups(self, parmenides, gsm_json, is_simplistic_rewriting):
        # Get all nodes from resulting graph and create list of Singletons
        number_of_nodes = range(len(gsm_json))
        self.create_all_singleton_nodes(gsm_json, number_of_nodes)

        # Check if conjugation ('conj') or ('multipleindobj') exists and if true exists, merge into SetOfSingletons
        # Also if 'compound' relationship is present, merge parent and child nodes
        for row in number_of_nodes:
            gsm_item = gsm_json[row]
            grouped_nodes = []
            has_conj = 'conj' in gsm_item['properties']
            has_multipleindobj = 'multipleindobj' in gsm_item['ell']
            is_compound = False
            group_type = None
            norm_confidence = 1.0
            if has_conj:
                conj = gsm_item['properties']['conj'].strip()
                if len(conj) == 0:
                    from LaSSI.structures.internal_graph.from_raw_json_graph import bfs
                    conj = bfs(gsm_json, gsm_item['id'])

                if 'and' in conj or 'but' in conj:
                    group_type = Grouping.AND
                elif ('nor' in conj) or ('neither' in conj):
                    group_type = Grouping.NEITHER
                elif 'or' in conj:
                    group_type = Grouping.OR
                else:
                    group_type = Grouping.NONE

            if has_conj or has_multipleindobj:
                for edge in gsm_item['phi']:
                    if 'orig' in edge['containment']:
                        node = self.nodes[edge['content']]
                        grouped_nodes.append(node)
                        norm_confidence *= node.confidence
            else:
                # If not 'conjugation' or 'multipleindobj', then check for compound edges
                is_compound, norm_confidence = self.get_compound_entities(grouped_nodes, gsm_json, gsm_item,
                                                                          is_compound,
                                                                          norm_confidence)

            if is_simplistic_rewriting and len(grouped_nodes) > 0:
                sorted_entities = sorted(grouped_nodes, key=lambda x: float(dict(x.properties)['pos']))
                sorted_entity_names = list(map(getattr, sorted_entities, repeat('named_entity')))

                all_types = list(map(getattr, sorted_entities, repeat('type')))
                specific_type = parmenides.most_specific_type(all_types)

                if group_type == Grouping.OR:
                    name = " or ".join(sorted_entity_names)
                elif group_type == Grouping.AND:
                    name = " and ".join(sorted_entity_names)
                elif group_type == Grouping.NEITHER:
                    name = " nor ".join(sorted_entity_names)
                    name = f"neither {name}"
                else:
                    name = " ".join(sorted_entity_names)

                self.nodes[gsm_item['id']] = Singleton(
                    id=gsm_item['id'],
                    named_entity=name,
                    properties=frozenset(gsm_item['properties'].items()),
                    min=min(grouped_nodes, key=lambda x: x.min).min,
                    max=max(grouped_nodes, key=lambda x: x.max).max,
                    type=specific_type,
                    confidence=norm_confidence
                )
            elif not is_simplistic_rewriting:
                self.create_set_of_singletons(group_type, grouped_nodes, gsm_item, has_conj, has_multipleindobj,
                                              is_compound, norm_confidence)

        self.remove_duplicate_nodes()

    # Phase 1.1
    def create_all_singleton_nodes(self, gsm_json, number_of_nodes):
        for row in number_of_nodes:
            gsm_item = gsm_json[row]
            if 'conj' in gsm_item['properties'] or 'multipleindobj' in gsm_item['ell']:
                continue  # Add SetOfSingletons later as we need ALL Singletons first
            else:
                min_value = -1
                max_value = -1
                node_type = "None"
                if len(gsm_item['xi']) > 0 and gsm_item['xi'][0] != '':
                    name = gsm_item['xi'][0]
                    min_value = int(gsm_item['properties']['begin'])
                    max_value = int(gsm_item['properties']['end'])
                    node_type = gsm_item['ell'][0] if len(gsm_item['ell']) > 0 else "None"
                else:
                    # xi might be empty if the node is invented, therefore existential
                    name = "?" + str(self.existentials.increaseAndGetExistential())
                    node_type = 'existential'

                self.nodes[gsm_item['id']] = Singleton(
                    id=gsm_item['id'],
                    named_entity=name,
                    properties=frozenset(gsm_item['properties'].items()),
                    min=min_value,
                    max=max_value,
                    type=node_type,
                    confidence=1.0
                )

    # Phase 1.2
    def get_compound_entities(self, grouped_nodes, gsm_json, gsm_item, is_compound, norm_confidence):
        gsm_id_json = {row['id']: row for row in gsm_json}  # Associate ID to key value
        edges_to_remove = []
        for idx, edge in enumerate(gsm_item['phi']):
            is_current_edge_compound = 'compound' in edge['containment']
            if is_current_edge_compound:
                is_compound = True
                child_node = self.nodes[edge['score']['child']]
                grouped_nodes.append(child_node)
                norm_confidence *= child_node.confidence

                child_gsm_item = gsm_id_json[edge['score']['child']]
                if len(child_gsm_item['phi']) > 0:
                    # Remove current compound edge
                    edges_to_remove.append(idx)
                    self.get_compound_entities(grouped_nodes, gsm_json, child_gsm_item, is_compound, norm_confidence)

        # Remove compound edges as no longer needed
        gsm_item['phi'] = [i for j, i in enumerate(gsm_item['phi']) if j not in edges_to_remove]
        return is_compound, norm_confidence

    # Phase 1.3
    def create_set_of_singletons(self, group_type, grouped_nodes, gsm_item, has_conj, has_multipleindobj, is_compound, norm_confidence):
        if has_conj:
            if group_type == Grouping.NEITHER:
                grouped_nodes = [
                    SetOfSingletons(id=x.id, type=Grouping.NOT, entities=tuple([x]), min=x.min, max=x.max,
                                    confidence=x.confidence) for x in grouped_nodes]
                grouped_nodes = tuple(grouped_nodes)
                group_type = Grouping.AND
            self.nodes[gsm_item['id']] = SetOfSingletons(
                id=gsm_item['id'],
                type=group_type,
                entities=tuple(grouped_nodes),
                min=min(grouped_nodes, key=lambda x: x.min).min,
                max=max(grouped_nodes, key=lambda x: x.max).max,
                confidence=norm_confidence
            )
        elif is_compound:
            grouped_nodes.insert(0, self.nodes[gsm_item['id']])
            new_id = self.freshId()
            self.node_id_map[gsm_item['id']] = new_id
            self.nodes[new_id] = SetOfSingletons(
                id=new_id,
                type=Grouping.GROUPING,
                entities=tuple(grouped_nodes),
                min=min(grouped_nodes, key=lambda x: x.min).min,
                max=max(grouped_nodes, key=lambda x: x.max).max,
                confidence=norm_confidence
            )
        elif has_multipleindobj:
            self.nodes[gsm_item['id']] = SetOfSingletons(
                id=gsm_item['id'],
                type=Grouping.MULTIINDIRECT,
                entities=tuple(grouped_nodes),
                min=min(grouped_nodes, key=lambda x: x.min).min,
                max=max(grouped_nodes, key=lambda x: x.max).max,
                confidence=norm_confidence
            )

    # Phase 1.4
    def remove_duplicate_nodes(self):
        ids_to_remove = set()
        for idx, node in self.nodes.items():
            for jdx, another_node in self.nodes.items():
                if (idx != jdx and isinstance(node, SetOfSingletons) and
                        isinstance(another_node, SetOfSingletons) and
                        node.type == another_node.type):
                    if set(node.entities).issubset(set(another_node.entities)):
                        ids_to_remove.add(idx)
        for x in ids_to_remove:
            self.nodes.pop(x)

    # Phase 2
    def associateNodeToMeuMatches(self, node, meu_db_row):
        # If key is SetOfSingletons, loop over each Singleton and make association to type
        # Giacomo: FIX, but only if they are not logical predicates
        from LaSSI.structures.internal_graph.EntityRelationship import SetOfSingletons
        if isinstance(node, SetOfSingletons) and ((node.type == Grouping.NONE) or (node.type == Grouping.GROUPING)):
            for entity in node.entities:
                self.associateNodeToMeuMatches(entity, meu_db_row)

            # merged_node = self.create_merged_node(node)  # So we can check if the concatenated name is in meuDB
            # self.associateNodeToMeuMatches(node, meu_db_row)
        elif isinstance(node, Singleton):
            # assign_type_to_singleton(item, stanza_row, nodes, key)
            self.associteNodeToBestMeuMatches(node, meu_db_row)

    # Phase 2.1
    def associteNodeToBestMeuMatches(self, item, stanza_row: MeuDB):
        # Loop over Stanza MEU and GSM result and evaluate overlapping words from chars
        for meu in stanza_row.multi_entity_unit:
            start_meu = meu.start_char
            end_meu = meu.end_char
            start_graph = item.min
            end_graph = item.max
            # https://scicomp.stackexchange.com/questions/26258/the-easiest-way-to-find-intersection-of-two-intervals/26260#26260
            if start_graph > end_meu or start_meu > end_graph:
                continue
            else:
                if not (start_graph > end_meu or start_meu > end_graph):
                    self.meu_entities[item].add(meu)
                    self.associations.add(item)

    # Phase 3
    def singletonTypeResolution(self):
        # if len(self.nodes) > 0:
        #     return self.nodes
        for item in self.associations:
            # for association in associations:
            if len(self.meu_entities[item]) > 0:
                if item.type == '∃' or item.type.startswith("JJ") or item.type.startswith("IN") or item.type.startswith(
                        "NEG"):
                    best_score = item.confidence
                    best_items = [item]
                    best_type = item.type
                else:
                    # TODO: Fix typing information (e.g. Golden Gate Bridge has GPE (0.8) and ENTITY (1.0)
                    best_score = max(map(lambda y: y.confidence, self.meu_entities[item]))
                    # best_items = [
                    #     y for y in self.meu_entities[item]
                    #     if (len(item.named_entity.split(' ')) == 1 and y.confidence == best_score) or
                    #        (y.confidence == best_score and
                    #         len(item.named_entity.split(' ')) > 1 and
                    #         y.monad is not None and
                    #        y.monad.lower() == item.named_entity.lower())
                    # ]
                    best_items = [
                        y for y in self.meu_entities[item]
                        if y.confidence == best_score
                    ]
                    best_type = None
                    if len(best_items) == 0:
                        return
                    if len(best_items) == 1:
                        best_item = best_items[0]
                        best_type = best_item.type
                    else:
                        best_types = list(set(map(lambda best_item: best_item.type, best_items)))
                        best_type = None
                        if len(best_types) == 1:
                            best_type = best_types[0]
                        ## TODO! type disambiguation, in future works, needs to take into account also the verb associated to it!
                        elif "PERSON" in best_types:
                            best_type = "PERSON"
                        elif "DATE" in best_types or "TIME" in best_types:
                            best_type = "DATE"
                        elif "GPE" in best_types:
                            best_type = "GPE"
                        elif "LOC" in best_types:
                            best_type = "LOC"
                        else:
                            best_type = "None"
                from LaSSI.structures.internal_graph.EntityRelationship import Singleton
                self.nodes[item.id] = Singleton(
                    id=item.id,
                    named_entity=item.named_entity,
                    properties=item.properties,
                    min=item.min,
                    max=item.max,
                    type=best_type,
                    confidence=best_score
                )

    ## Phase 4
    def resolveGraphNERs(self, is_simplistic_rewriting, parmenides, meu_db_row):
        for key in self.nodes:
            node = self.nodes[key]
            # association = nbb[item]
            # best_score = association.confidence
            if not isinstance(node, SetOfSingletons):
                self.ConfidenceAndEntityExpand(key)

        for key in self.nodes:
            node = self.nodes[key]
            # association = nbb[item]
            # best_score = association.confidence
            if isinstance(node, SetOfSingletons):
                self.ConfidenceAndEntityExpand(key)

        # With types assigned, merge SetOfSingletons into Singleton
        for key in self.nodes:
            node = self.nodes[key]
            if isinstance(node, SetOfSingletons):
                from LaSSI.ner.MergeSetOfSingletons import GraphNER_withProperties
                if node.type == Grouping.MULTIINDIRECT:
                    # # If entities is only 1 item, then we can simply replace the item.id
                    # if len(node.entities) == 1:
                    #     entity = node.entities[0]
                    #     if isinstance(entity, SetOfSingletons):
                    #         self.nodes[node.id] = GraphNER_withProperties(entity, is_simplistic_rewriting, meu_db_row,
                    #                                                       parmenides, existentials)
                    #     else:
                    #         self.node_id_map[node.id] = entity.id
                    #         self.nodes[node.id] = self.ConfidenceAndEntityExpand(entity.id)
                    # # If more than 1 item, then we replace the entity.id for each orig of the 'multiindirectobj'
                    # else:
                    #     # nodes[item.id] = associate_to_container(item, nbb)
                    for entity in node.entities:
                        if isinstance(entity, SetOfSingletons):
                            self.nodes[entity.id] = GraphNER_withProperties(entity, is_simplistic_rewriting,
                                                                            meu_db_row, parmenides, self.existentials)
                        else:
                            self.node_id_map[node.id] = entity.id
                            self.ConfidenceAndEntityExpand(entity.id)
                if node.type == Grouping.GROUPING:
                    self.nodes[key] = GraphNER_withProperties(node, is_simplistic_rewriting, meu_db_row, parmenides, self.existentials)

    # Phase 4.1
    def ConfidenceAndEntityExpand(self, key):
        from LaSSI.structures.internal_graph.EntityRelationship import SetOfSingletons
        item = self.nodes[key]
        if isinstance(item, SetOfSingletons):
            confidence = 1.0
            new_set = list(map(lambda x: self.ConfidenceAndEntityExpand(x.id), item.entities))
            for entity in new_set:
                confidence *= entity.confidence
            set_item = SetOfSingletons(
                id=item.id,
                type=item.type,
                entities=tuple(new_set),
                min=item.min,
                max=item.max,
                confidence=confidence
            )
            self.nodes[key] = set_item
        else:
            if item.id not in self.nodes:
                self.nodes[item.id] = item
                return item
            else:
                return self.nodes[item.id]

    # Phase 5
    def checkForNegation(self, gsm_json):
        # Check for negation in node 'xi' and 'properties'
        grouped_nodes = []
        for row in range(len(gsm_json)):
            gsm_item = gsm_json[row]  # Find node in JSON so we can get child nodes
            key = gsm_item['id']
            node = self.nodes[key]  # Singleton node

            # # TODO: Check if xi is in negations or NEG is in ell
            # if len(gsm_item['xi']) > 0 and gsm_item['xi'][0] in self.negations or 'NEG' in gsm_item['ell']:
            #     is_prop_negated = True

            # Check if name is not/no or if negation found in properties
            ## TODO: there was a case where node didn't have the field named_entity
            ##       Please double check if this will fix it...
            # if is_prop_negated:
            #     self.nodes[key] = SetOfSingletons(
            #         id=key,
            #         type=Grouping.NOT,
            #         entities=tuple([node]),
            #         min=node.min,
            #         max=node.max,
            #         confidence=node.confidence
            #     )
            # else:
            # if hasattr(node, "named_entity") and node.named_entity in self.negations:
            grouped_nodes.append(node)
            for edge in gsm_item['phi']:
                # if edge['score']['child'] in self.nodes:  # Node might have been removed so check key exists
                child = self.nodes[self.get_node_id(edge['score']['child'])]
                if (hasattr(child, "named_entity") and child.named_entity in self.negations) or child.type == 'NEG':
                    # grouped_nodes.append(child)
                    fresh_id = self.freshId()
                    self.node_id_map[key] = fresh_id
                    self.nodes[fresh_id] = SetOfSingletons(
                        id=fresh_id,
                        type=Grouping.NOT,
                        entities=tuple(grouped_nodes),
                        min=min(grouped_nodes, key=lambda x: x.min).min,
                        max=max(grouped_nodes, key=lambda x: x.max).max,
                        confidence=child.confidence
                    )
            grouped_nodes = []

    def constructIntermediateGraph(self, gsm_json, rejected_edges, non_verbs) -> Graph:
        self.edges = []
        for row, gsm_item, edge in self.iterateOverEdges(gsm_json, rejected_edges):
            edge_label_name = edge['containment']  # Name of edge label
            if 'amod' in edge_label_name: # TODO: Is this 'amod' or just 'mod' as could have 'nmod' (e.g. (traffic)-[nmod]->(Newcastle)
                source_node = self.nodes[self.get_node_id(edge['score']['parent'])]
                target_node = self.nodes[self.get_node_id(edge['score']['child'])]
                temp_prop = dict(copy(source_node.properties))
                if isinstance(target_node, Singleton):
                    type_key = get_node_type(target_node)
                else:
                    from LaSSI.external_services.Services import Services
                    type_key = self.services.getParmenides().most_general_type(
                        map(lambda x: x.type, target_node.entities))

                if type_key not in temp_prop:
                    temp_prop[type_key] = (target_node,)
                elif not isinstance(temp_prop[type_key], list):
                    temp_prop[type_key] = (temp_prop[type_key], target_node)
                else:
                    temp_prop[type_key] += (target_node,)

                self.nodes[self.get_node_id(edge['score']['parent'])] = Singleton(
                    id=source_node.id,
                    named_entity=source_node.named_entity,
                    properties=frozenset(temp_prop.items()),
                    min=source_node.min,
                    max=source_node.max,
                    type=source_node.type,  # TODO: This type is the ENUM case (i.e. 3 instead of NOT) so use dacite
                    confidence=source_node.confidence
                )

        for row in range(len(gsm_json)):
            gsm_item = gsm_json[row]

            if len(gsm_item['phi']) == 0:
                source_node_id = gsm_item['id']
                target_node_id = None
                source_node = self.resolve_node_id(source_node_id)
                if isinstance(source_node, Singleton):
                    source_properties = dict(source_node.properties)
                    if 'action' in source_properties:
                        edge_label_name = source_properties['action']
                        parmenides_types = {str(x)[len(Parmenides.parmenides_ns):] for x in self.services.getParmenides().typeOf(edge_label_name)}
                        if 'Verb' in parmenides_types or len(parmenides_types) == 0:
                            self.create_edges(edge_label_name, gsm_item, non_verbs, source_node_id, target_node_id)

            for edge in gsm_item['phi']:
                # Check if child node has 'multipleindobj' type, to reassign edges from each 'orig'
                child_gsm_item = self.get_gsm_item_from_id(gsm_json, edge['score']['child'])
                if 'multipleindobj' in child_gsm_item['ell']:
                    edge_label_name = edge['containment']  # Name of edge label

                    source_node_id = edge['score']['parent']

                    for child_edge in child_gsm_item['phi']:
                        target_node_id = child_edge['score']['child']
                        self.create_edges(edge_label_name, gsm_item, non_verbs, source_node_id, target_node_id)

                # Make sure current edge is not in list of rejected edges, e.g. 'compound'
                if edge['containment'] not in rejected_edges:
                    if 'orig' not in edge['containment']:
                        edge_label_name = edge['containment']  # Name of edge label

                        source_node_id = edge['score']['parent']
                        target_node_id = edge['score']['child']
                        self.create_edges(edge_label_name, gsm_item, non_verbs, source_node_id, target_node_id)

        print(json.dumps(Graph(self.edges), cls=EnhancedJSONEncoder))
        return Graph(self.edges)

    def iterateOverEdges(self, gsm_json, rejected_edges):
        for row in range(len(gsm_json)):
            gsm_item = gsm_json[row]
            for edge in gsm_item['phi']:
                # Make sure current edge is not in list of rejected edges, e.g. 'compound'
                if edge['containment'] not in rejected_edges:
                    if 'orig' not in edge['containment']:
                        yield row, gsm_item, edge

    def create_edges(self, edge_label_name, gsm_item, non_verbs, source_node_id, target_node_id):
        source_node = self.resolve_node_id(source_node_id)
        target_node = self.resolve_node_id(target_node_id)

        has_negations = self.does_string_have_negations(edge_label_name)

        # Giacomo: the former code was not able to handle: "does not play"
        query_words = edge_label_name.split()
        result_words = [word for word in query_words if word.lower() not in self.negations]
        edge_label_name = ' '.join(result_words)

        # Check if name of edge is in "non verbs"
        edge_type = "verb"
        for non_verb in non_verbs:
            if edge_label_name == non_verb.strip():
                edge_type = "non_verb"
                break
        if self.nodes[source_node_id].type == Grouping.MULTIINDIRECT:  # TODO: I don't think this ever happens?
            for node in self.nodes[source_node_id].entities:
                self.edges.append(Relationship(
                    source=source_node,
                    target=self.resolve_node_id(node.id),
                    edgeLabel=Singleton(id=gsm_item['id'], named_entity=edge_label_name,
                                        properties=frozenset(dict().items()),
                                        min=-1,
                                        max=-1, type=edge_type,
                                        confidence=self.nodes[gsm_item['id']].confidence),
                    isNegated=has_negations
                ))
        elif 'amod' not in edge_label_name and 'neg' not in edge_label_name:
            self.edges.append(Relationship(
                source=source_node,
                target=target_node,
                edgeLabel=Singleton(id=gsm_item['id'], named_entity=edge_label_name,
                                    properties=frozenset(dict().items()),
                                    min=-1,
                                    max=-1, type=edge_type,
                                    confidence=self.nodes[gsm_item['id']].confidence),
                isNegated=has_negations,
            ))

    def constructSentence(self, transitive_verbs) -> List[Sentence]:
        from LaSSI.structures.kernels.Sentence import create_sentence_obj
        return create_sentence_obj(self.edges, self.nodes, transitive_verbs, self.negations)

import itertools
from collections import defaultdict
from itertools import repeat


def score_from_meu(min_value, max_value, node_type, meu_db_row, parmenides):
    # max_value = max_value
    matched_meus = []

    for meu in meu_db_row.multi_entity_unit:
        start_meu = meu.start_char
        end_meu = meu.end_char
        if min_value == start_meu and end_meu == max_value:
            # TODO: mgu or its opposite...
            if (parmenides.most_specific_type([node_type, meu.type]) == meu.type or
                    node_type == meu.type or
                    node_type == "None"):
                matched_meus.append(meu)

    if len(matched_meus) == 0:
        return 0, "None"
    else:
        max_score = max(map(lambda x: x.confidence, matched_meus))
        return max_score, parmenides.most_specific_type(
            list(map(lambda x: x.type, filter(lambda x: x.confidence == max_score, matched_meus))))


def GraphNER_withProperties(node, is_simplistic_rewriting, meu_db_row, parmenides, existentials):
    from LaSSI.structures.internal_graph.EntityRelationship import Singleton
    from LaSSI.utils.allChunks import allChunks
    import numpy

    chosen_entity = None
    extra = ""
    norm_confidence = 1
    fusion_properties = dict()

    # Sort entities based on word position to keep correct order
    sorted_entities = sorted(node.entities, key=lambda x: float(dict(x.properties)['pos']))

    sorted_entity_names = list(map(getattr, sorted_entities, repeat('named_entity')))
    d = dict(zip(range(len(sorted_entity_names)), sorted_entity_names))  # dictionary for storing the replacing elements

    layered_alternatives = defaultdict(list)
    for x in allChunks(list(d.keys())):
        layered_alternatives[len(x)].append(x)

    for layer in layered_alternatives.values():
        max_score = -1
        alternatives = []
        for x in layer:
            # if all(y in set(map(lambda z: z[0], itertools.chain(map(lambda t: (t, ) if isinstance(t, int) else t, d.keys())))) for y in x):
            exp = " ".join(map(lambda z: sorted_entity_names[z], x))
            min_value = min(map(lambda z: sorted_entities[z].min, x))
            max_value = max(map(lambda z: sorted_entities[z].max, x))

            all_types = [sorted_entities[z].type for z in x]
            specific_type = parmenides.most_specific_type(all_types)
            candidate_meu_score, candidate_meu_type = score_from_meu(min_value, max_value, specific_type,
                                                                     meu_db_row, parmenides)
            all_meu_score_prod = numpy.prod(list(map(lambda z: sorted_entities[z].confidence, x)))

            # if (score_from_meu(exp, min_value, max_value, specific_type, stanza_row) >=
            #         numpy.prod(list(map(lambda z: score_from_meu(sorted_entities[z].named_entity, sorted_entities[z].min, max_value, sorted_entities[z].type, stanza_row), x)))):
            if ((candidate_meu_score >= all_meu_score_prod) or ((specific_type != candidate_meu_type) and (
                    parmenides.most_specific_type([specific_type, candidate_meu_type]) == candidate_meu_type))):
                if (candidate_meu_score > max_score):
                    alternatives = [(x, all_meu_score_prod, exp)]
                    max_score = candidate_meu_score

        if len(alternatives) > 0:
            alternatives.sort(key=lambda x: x[1])
            candidate_delete = set()
            x = alternatives[-1]
            for k, v in d.items():
                if isinstance(k, int):
                    if k in x[0]:
                        candidate_delete.add(k)
                    elif isinstance(k, tuple):
                        if len(set(x[0]).intersection(set(k))) > 0:
                            candidate_delete.add(k)
            for z in candidate_delete:
                d.pop(z)
            d[x[0]] = x[2]
    print(d) # TODO: "d" is sometimes producing more than one set of values...
    print("OK")

    # for entity in sorted_entities:
    #     norm_confidence *= entity.confidence
    #     fusion_properties = fusion_properties | dict(entity.properties)  # TODO: Most properties are overwritten?
    #     # Giacomo: then, we'd need to use a defaultdict(list) and merge them as https://stackoverflow.com/a/70689832/1376095
    #     if entity.type != "ENTITY" and entity.type != 'noun' and not simplistic and chosen_entity is None:  # TODO: FIX CHOSEN ENTITY NOT NONE
    #         chosen_entity = entity
    #     else:
    #         extra = " ".join((extra, entity.named_entity))  # Ensure there is no leading space

    # TODO: Remove time-space information and add as properties
    for entity in sorted_entities:
        norm_confidence *= entity.confidence
        fusion_properties = fusion_properties | dict(entity.properties)  # TODO: Most properties are overwritten?
        if entity.named_entity == list(d.values())[0]:
            chosen_entity = entity
        else:
            extra = " ".join((extra, entity.named_entity))  # Ensure there is no leading space

    extra = extra.strip()  # Remove whitespace

    if is_simplistic_rewriting:
        new_properties = {
            "specification": "none",
            "begin": str(sorted_entities[0].min),
            "end": str(sorted_entities[len(sorted_entities) - 1].max),
            "pos": str(dict(sorted_entities[0].properties)['pos']),
            "number": "none"
        }

        new_properties = new_properties | fusion_properties

        if chosen_entity is not None:
            node_type = chosen_entity.type
        else:
            node_type = "ENTITY"

        merged_node = Singleton(
            id=node.id,
            named_entity=extra,
            properties=frozenset(new_properties.items()),
            min=sorted_entities[0].min,
            max=sorted_entities[len(sorted_entities) - 1].max,
            type=node_type,  # TODO: What should this type be?
            confidence=norm_confidence
        )
    elif chosen_entity is None:  # Not simplistic
        sing_type = 'None'
        min_value = sorted_entities[0].min
        max_value = sorted_entities[len(sorted_entities) - 1].max

        if extra != '':
            name = extra
            extra = ''
        else:
            sing_type = 'existential'
            name = "?" + str(existentials.increaseAndGetExistential())

        # New properties for ? object
        new_properties = {
            "specification": "none",
            "begin": str(min_value),
            "end": str(max_value),
            "pos": str(dict(sorted_entities[0].properties)['pos']),
            "number": "none",
            "extra": extra
        }

        new_properties = new_properties | fusion_properties

        candidate_meu_score, candidate_meu_type = score_from_meu(min_value, max_value, sing_type,
                                                                 meu_db_row, parmenides)

        merged_node = Singleton(
            id=node.id,
            named_entity=name,
            properties=frozenset(new_properties.items()),
            min=min_value,
            max=max_value,
            type=candidate_meu_type,
            confidence=candidate_meu_score
        )
    elif chosen_entity is not None:  # Not simplistic and found chosen entity
        # Convert back from frozenset to append new "extra" attribute
        new_properties = dict(chosen_entity.properties)
        new_properties["extra"] = extra

        merged_node = Singleton(
            id=node.id,
            named_entity=chosen_entity.named_entity,
            properties=frozenset(new_properties.items()),
            min=sorted_entities[0].min,
            max=sorted_entities[len(sorted_entities) - 1].max,
            type=chosen_entity.type,
            confidence=norm_confidence
        )
    else:
        print("Error")
        merged_node = None

    return merged_node

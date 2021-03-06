from collections import OrderedDict
from functools import lru_cache
from typing import Dict, List
import numpy as np
from scipy import stats

import utils
import vectors
from abstract_graph import HiGraph, HiNode

LOG = utils.get_logger(__name__, stream='INFO', file='WARNING')

COUNT = 0


class VectorNode(HiNode):
    """A node in a VectorGraph.

    Note that all VectorNodes must have a parent VectorGraph. However, they
    do not necessarily need to be in the graph as such.

    Attributes:
        string: e.g. [the [big dog]]
        idx: an int identifier
        id_vec: a random sparse vector that never changes
    """
    def __init__(self, graph, id_string, children=(), row_vecs={}):
        super().__init__(graph, id_string, children)
        self.id_vec = graph.vector_model.sparse()

        # Multiple edge types can be implemented by using a separate vector
        # for each node, hence multiple row vectors. This isn't discussed in
        # the paper, and all the presented simulations use a single row vector.
        self.row_vecs = {row: graph.vector_model.sparse() * graph.INITIAL_ROW
                         for row in graph.rows}
        self.row_vecs.update(row_vecs)

        if self.graph.DYNAMIC:
            self.dynamic_id_vecs = {row: graph.vector_model.sparse()
                                    for row in graph.rows}
            self.dynamic_row_vecs = {row: np.copy(vec)
                                     for row, vec in self.row_vecs.items()}


    def bump_edge(self, node, edge='default', factor=1):
        """Increases the weight of an edge to another node."""
        assert edge in self.graph.edges
        
        # If each edge has its own row vector, we use that vector,
        # otherwise we use the single row vector.
        row = edge if self.graph.EDGE_ROWS else '_row'

        # Add other node's id_vec to this node's row_vec
        labeled_id = self.graph.vector_model.label(node.id_vec, edge)
        self.row_vecs[row] += labeled_id * factor    

        if self.graph.DYNAMIC:
            # This node's dynamic row vectors point to nodes that 
            # other nodes that point to target node point to.
            self.dynamic_row_vecs[row] += \
                vectors.normalize(node.dynamic_id_vecs[row]) * factor
            # The target node learns that this node points to it.
            node.dynamic_id_vecs[row] += self.row_vecs[row] * factor

        #self.edge_weight.cache_clear()

    #@lru_cache(maxsize=None)
    @utils.contract(lambda x: 0 <= x <= 1)
    def edge_weight(self, node, edge='default', generalize=False):
        """Returns the weight of an edge to another node.

        Between 0 and 1 inclusive.
        """
        assert edge in self.graph.edges

        row = edge if self.graph.EDGE_ROWS else '_row'
        row_vec = self.row_vecs[row]

        if generalize:
            form, factor = generalize
            normalize = vectors.normalize  # optimization

            # Get gen_vec, a generalized form of row_vec.
            if form == 'dynamic':
                gen_vec = self.dynamic_row_vecs[row]
                
            elif form == 'similarity':
                sims = np.array([self.similarity(n) for n in self.graph.nodes])
                all_row_vecs = np.array([normalize(n.row_vecs[row])
                                         for n in self.graph.nodes])
                gen_vec = sims @ all_row_vecs  # matrix multiplication
            
            # Add the generalized row_vec to the original row_vec.
            row_vec = (normalize(row_vec) * (1 - factor)
                       + normalize(gen_vec) * factor)

        labeled_id = self.graph.vector_model.label(node.id_vec, edge)
        weight = vectors.cosine(row_vec, labeled_id)
        return max(weight, 0.0)

    @utils.contract(lambda x: 0 <= x <= 1)
    def similarity(self, node):
        """Weighted geometric mean of cosine similarities for each row."""
        edge_sims = [max(0.0, vectors.cosine(self.row_vecs[row], node.row_vecs[row]))
                     for row in self.row_vecs]
        
        return min(1.0, stats.gmean(edge_sims))  # clip precision error


class VectorGraph(HiGraph):
    """A graph represented with high dimensional sparse vectors."""
    def __init__(self, edges=None, DIM=10000, PERCENT_NON_ZERO=0.005, 
                 BIND_OPERATION='addition', HIERARCHICAL=True, EDGE_ROWS=False,
                 COMPOSITION=False, DECAY=0, DYNAMIC=False, INITIAL_ROW=1, **kwargs):
        # TODO: kwargs is just so that we can pass more parameters than are
        # actually used.
        super().__init__()
        self.edges = edges or ['default']
        self.DIM = DIM
        self.PERCENT_NON_ZERO = PERCENT_NON_ZERO
        self.BIND_OPERATION = BIND_OPERATION
        self.HIERARCHICAL = HIERARCHICAL
        self.EDGE_ROWS = EDGE_ROWS
        self.COMPOSITION = COMPOSITION
        self.DECAY = DECAY
        self.DYNAMIC = DYNAMIC
        self.INITIAL_ROW = INITIAL_ROW
        self.vector_model = vectors.VectorModel(self.DIM,
                                                self.PERCENT_NON_ZERO,
                                                self.BIND_OPERATION)
        self.rows = edges if EDGE_ROWS else ['_row']

    def create_node(self, id_string):
        return VectorNode(self, id_string)

    def bind(self, *nodes, composition=None):
        if self.HIERARCHICAL:
            children = nodes
        else:
            children = self._concatenate_children(nodes)
        if composition is None:
            composition = self.COMPOSITION

        row_vecs = {}
        if composition:
            # gen_vec is the weighted average of all other blobs with
            # the same number of children.
            gen_vecs = {row: self.vector_model.zeros() for row in self.rows}
            comparable = (n for n in self.nodes if len(n.children) == len(children))
            for node in comparable:
                child_sims = [my_child.similarity(other_child)
                              for my_child, other_child in zip(children, node.children)]
                total_sim = stats.gmean(child_sims)
                for row, vec in gen_vecs.items():
                    vec += vectors.normalize(node.row_vecs[row]) * total_sim

            row_vecs = {row: vec * composition for row, vec in gen_vecs.items()}
            
            assert not np.isnan(np.sum(list(row_vecs.values())))

        id_string = self._id_string(children)
        return VectorNode(self, id_string, children=children, row_vecs=row_vecs)

    def sum(self, nodes, weights=None, id_string='__SUM__', id_vec=None):
        weights = weights or np.ones(len(nodes))
        assert len(weights) == len(nodes)

        row_vecs = {edge: sum(n.row_vecs[edge] * w 
                              for n, w in zip(nodes, weights))
                    for edge in self.edges}

        if id_vec is None:
            id_vec = sum(n.id_vec * w for n, w in zip(nodes, weights))

        return VectorNode(self, id_string, id_vec, row_vecs)

    def decay(self):
        """Decays all learned connections between nodes."""
        assert False, 'unimplimented'
        decay = self.DECAY
        if not decay:
            return
        for node in self.nodes:
            node.row_vec += node._original_row * decay


if __name__ == '__main__':
    #import pytest
    #pytest.main(['test_graph.py'])
    graph = VectorGraph(edges='12')
    a = graph.create_node('a')
    b = graph.create_node('b')
    graph.add(a)
    graph.add(b)

    print(a.edge_weight(b, ))
    a.bump_edge(b)
    print(a.edge_weight(b))



from collections import Counter, OrderedDict, deque
import itertools
from typing import Dict, List
from typed import typechecked
import numpy as np
import yaml
import utils
import vectors


LOG = utils.get_logger(__name__, stream='INFO', file='WARNING')


class Node(object):
    """A Node in a graph.

    Attributes:
        string: e.g. [the [big dog]]
        idx: an int identifier
        forward_edges: the number of times each other node in the graph has been
                 after this node in the memory window
        backward_edges: the number of times each other node in the graph has been
                 before this node in the memory window
        id_vector: a random sparse vector that never changes
    """
    def __init__(self, graph, string, id_vector) -> None:
        self.graph = graph
        self.params = graph.params
        self.string = string
        self.idx = None  # set when the Node is added to graph

        self.id_vector = id_vector
        self.semantic_vec = np.copy(self.id_vector)
        
        self.count = 0
        self.forward_edges = Counter()
        self.backward_edges = Counter()

    @property
    def context_vec(self) -> np.ndarray:
        """Represents this node in context.

        This vector is only used in permuted forms, `preced_vec` and `follow_vec`.
        These two vectors are used when updating temporal weights. When another
        node follows this node, this node's `preced_vec` will be added to that
        node's semantic vector.

        `context_vec` is a combination of the semantic vector and id vector,
        weighted by the SEMANTIC_TRANSFER parameter."""
        return (self.semantic_vec * self.params['SEMANTIC_TRANSFER'] +
                self.id_vector * (1 - self.params['SEMANTIC_TRANSFER']))

    @property
    def precede_vec(self) -> np.ndarray:
        """Represents this node occurring before another node"""

        # roll shifts every element in the arry
        # [1,2,3,4] -> [4, 1, 2, 3]

        # We use the two largest primes < 100 for the shift values
        # in order to minimize interference between the two.
        return np.roll(self.context_vec, 89)

    @property
    def follow_vec(self) -> np.ndarray:
        """Represents this node occurring after another node"""
        return np.roll(self.context_vec, 97)

    def distribution(self, kind, use_vectors=True, exp=1):
        """A statistical distribution defined by this nodes edges.

        This is used for introspection and `speak_markov` thus it
        is not part of the core of the model"""
        if use_vectors:
            # get the appropriate list of data based on 
            # the kind of distribution we want
            if kind is 'following':
                data = [vectors.cosine(self.semantic_vec, node.follow_vec)
                         for node in self.graph.nodes]
            elif kind is 'preceding':
                data = [vectors.cosine(self.semantic_vec, node.precede_vec)
                         for node in self.graph.nodes]
            elif kind is 'chunking':
                data = [self.graph.chunkability(self, node) for node in self.graph.nodes]
            else:
                raise ValueError('{kind} is not a valid distribution.'
                                 .format_map(locals()))

            distribution = (np.array(data) + 1.0) / 2.0  # probabilites must be non-negative
            distribution **= exp  # accentuate differences
            return distribution / np.sum(distribution)

        else:  # use counts
            if kind is 'following':
                edge = 'forward_edges'
            elif kind is 'preceding':
                edge = 'backward_edges'
            else:
                raise ValueError('{kind} is not a valid distribution.'
                                 .format_map(locals()))

            counts = np.array([getattr(self, edge)[node2]
                               for node2 in self.graph.nodes])
            total = np.sum(counts)
            if total == 0:
                # This can happen for a node that never ends/begins an 
                # utterance. For example, '[ate #]' never begins an utterance.
                return counts
            else:
                return counts / np.sum(counts)

    def predict(self, kind, **distribution_args):
        """Returns a node that is likely to follow this node."""
        distribution = self.distribution(kind, **distribution_args)
        return np.random.choice(self.graph.nodes, p=distribution)

    def __hash__(self) -> int:
        if self.idx is not None:
            return self.idx
        else:
            return str(self).__hash__()

    def __repr__(self):
        return self.string

    def __str__(self):
        return self.string


class Parse(list):
    """A parse of an utterance represented as a list of Nodes. 

    The parse is computed upon intialization. This computation has side
    effects for the parent Numila instance (i.e. learning). The loop
    in __init__ is thus the learning algorithm.
    """
    def __init__(self, graph, utterance, learn=True, verbose=False) -> None:
        super().__init__()
        self.graph = graph
        self.params = graph.params
        self.learn = learn
        self.memory = deque(maxlen=self.params['MEMORY_SIZE'])
        self.log = print if verbose else lambda *args: None  # a dummy function
        self.log('\nPARSING', '"', ' '.join(utterance), '"')


        # This is the "main loop" of the model, i.e. it will run once for
        # every token in the training corpus. The four functions in the loop
        # correspond to the four steps of the learning algorithm.
        for token in utterance:
            #self.graph.decay()
            self.shift(token)
            self.update_weights()
            if len(self.memory) == self.params['MEMORY_SIZE']:
                # Only attempt to chunk after filling memory.
                self.try_to_chunk()  # always decreases number of nodes in memory by 1

        self.log('no more tokens')
        # Process the tail end.
        while self.memory:  # there are nodes left to be processed
            self.update_weights()
            self.try_to_chunk()


    def shift(self, token) -> None:
        """Adds a token to memory, creating a new node in the graph if new.

        If 4 or more nodes are already in memory, shifting a new token will
        implicitly drop the least recent node from memory.
        """
        assert len(self.memory) < self.memory.maxlen

        self.log('shift: {token}'.format_map(locals()))
        try:
            node = self.graph[token]
        except KeyError:  # a new token
            node = self.graph.create_token(token)
            if self.learn:
                self.graph.add_node(node)
        self.memory.append(node)

    def update_weights(self) -> None:
        """Strengthens the connection between every adjacent pair of Nodes in memory.

        For a pair (the, dog) we increase the weight of  the forward edge,
        the -> dog, and the backward edge, dog -> the."""

        if not self.learn:
            return

        self.log('memory =', self.memory)

        

        # We have to make things a little more complicated to avoid
        # updating based on vectors changed in this round of updates.

        factor = self.params['LEARNING_RATE'] * self.params['FTP_PREFERENCE'] 
        ftp_updates = {node: (node.follow_vec * factor)
                       for node in self.memory}

        factor = self.params['LEARNING_RATE'] * (1 - self.params['FTP_PREFERENCE']) 
        btp_updates = {node: (node.precede_vec * factor)
                       for node in self.memory}

        for node, next_node in utils.neighbors(self.memory):
            self.log('  -> strengthen: {node} & {next_node}'.format_map(locals()))

            node.forward_edges[next_node] += 1
            next_node.backward_edges[node] += 1
            
            node.semantic_vec += ftp_updates[next_node]
            next_node.semantic_vec += btp_updates[node]

    def try_to_chunk(self) -> None:
        """Attempts to combine two Nodes in memory into one Node.

        If no Node pairs form a chunk, then the oldest node in memory is
        dropped from memory. Thus, this method always reduces the numbe of
        nodes in memory by 1."""
        #memory = self[-memory_size:]

        if len(self.memory) == 1:
            # We can't create a chunk when there's only one node left.
            # This can only happen while processing the tail, so we
            # must be done processing
            self.append(self.memory.popleft())
            return
        
        chunkabilities = [self.graph.chunkability(node, next_node)
                          for node, next_node in utils.neighbors(self.memory)]
        self.log('chunkabilities =', chunkabilities)
        best_idx = np.argmax(chunkabilities)

        chunk = self.graph.get_chunk(self.memory[best_idx], self.memory[best_idx+1])
        if chunk:
            # combine the two nodes into one chunk
            self.log(('  -> create chunk: {chunk}').format_map(locals()))
            chunk.count += 1
            self.memory[best_idx] = chunk
            del self.memory[best_idx+1]
        else:  # can't make a chunk
            # We remove the oldest node to make room for a new one.
            self.append(self.memory.popleft())
            self.log('  -> no chunk created')

    def __str__(self):
        string = super().__str__().replace(',', ' |')[1:-1]
        return '(' + string + ')'



class Numila(object):
    """The premier language acquisition model."""
    def __init__(self, param_file='params.yml', **parameters) -> None:
        # read parameters from file, overwriting with keyword arguments
        with open(param_file) as f:
            self.params = yaml.load(f.read())
        self.params.update(parameters)
        self.vector_model = vectors.VectorModel(self.params['DIM'],
                                                self.params['PERCENT_NON_ZERO'],
                                                self.params['BIND_OPERATION'])
        
        # Each token gets an int ID which specifies its index
        # in self.nodes and self.activations.
        self.string_to_index = OrderedDict()  # type: Dict[str, int]
        self.nodes = []
        self.activations = np.zeros(100000)

    def parse_utterance(self, utterance, verbose=False):
        self.decay()
        if isinstance(utterance, str):
            utterance = utterance.split(' ')
        utterance = ['#'] + utterance + ['#']
        return Parse(self, utterance, verbose)

    def create_token(self, token_string) -> Node:
        """Add a new base token to the graph."""
        id_vector = self.vector_model.sparse()
        return Node(self, token_string, id_vector)

    def create_chunk(self, node1, node2) -> Node:
        """Returns a chunk composed of node1 and node2.

        This just creates a node. It does not add it to the graph."""
        chunk_string = '[{node1.string} {node2.string}]'.format_map(locals())
        id_vector = self.vector_model.bind(node1.id_vector, node2.id_vector)
        return Node(self, chunk_string, id_vector)

    def add_node(self, node) -> None:
        """Adds a node to the graph."""
        idx = len(self.nodes)
        node.idx = idx
        self.string_to_index[str(node)] = idx
        self.nodes.append(node)
        self.activations[idx] = 1.0

    def get_chunk(self, node1, node2, force=False) -> Node:
        """Returns a chunk of node1 and node2 if the chunk is in the graph.

        If the chunk doesn't exist, we check if the pair is chunkable
        enough for a new chunk to be created. If so, the new chunk is returned.
        """
        # This is slightly magic. We find the unique string identifier for a chunk
        # by concatenating the identifiers for the constituents.
        chunk_string = '[{node1.string} {node2.string}]'.format_map(locals())
        if chunk_string in self:
            return self[chunk_string]
        else:
            # consider making a new node
            if self.chunkability(node1, node2) > self.params['CHUNK_THRESHOLD']:
                chunk = self.create_chunk(node1, node2)
                self.add_node(chunk)
                return chunk
            else:
                if force:
                    return self.create_chunk(node1, node2)
                else:
                    return None

    def chunkability(self, node1, node2) -> float:
        """How well two nodes form a chunk.

        The geometric mean of forward transitional probability and
        bakward transitional probability."""

        ftp = vectors.cosine(node1.semantic_vec, node2.follow_vec)
        btp = vectors.cosine(node1.precede_vec, node2.semantic_vec)
        return (ftp * btp) ** 0.5

    def decay(self) -> None:
        """Decays all learned connections between nodes.

        This is done by adding a small factor of each nodes id_vector to
        its semantic vector, effectively making each node more similar
        to its initial state"""
        for node in self.nodes:
            node.semantic_vec += node.id_vector * self.params['DECAY_RATE']

    def speak_markov(self, **distribution_args):
        utterance = [self['#']]
        for _ in range(20):
            nxt = utterance[-1].predict('following', **distribution_args)
            utterance.append(nxt)
            if '#' in str(nxt):  # could be a chunk with boundary char
                break
        return ' '.join(map(str, utterance))

    def speak(self, words, verbose=False):
        log = print if verbose else lambda *args: None
        nodes = [self[w] for w in words]

        # combine the two chunkiest nodes into a chunk until only one node left
        while len(nodes) > 1:
            n1, n2 = max(itertools.permutations(nodes, 2),
                         key=lambda pair: self.chunkability(*pair))
            nodes.remove(n1)
            nodes.remove(n2)
            # Using `force=True` returns the best chunk, regardless of 
            # whether it is a  chunk in the graph or not.
            chunk = self.get_chunk(n1, n2, force=True)
            log('\tchunk:', chunk)
            nodes.append(chunk)

        return nodes[0]


    def __getitem__(self, node_string) -> Node:
        try:
            idx = self.string_to_index[node_string]
            return self.nodes[idx]
        except KeyError:
            raise KeyError('{node_string} is not in the graph.'.format_map(locals()))

    def __contains__(self, item) -> bool:
        assert isinstance(item, str)
        return item in self.string_to_index

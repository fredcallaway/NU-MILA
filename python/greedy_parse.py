import utils
from collections import deque
import numpy as np
from scipy import stats

class GreedyParse(list):
    """A parse of an utterance represented as a list of Nodes.

    The parse is computed upon intialization. This computation has side
    effects for the parent Numila instance (i.e. learning). The loop
    in __init__ is thus both the comprehension and learning algorithm.
    """
    def __init__(self, model, utterance, learn=True) -> None:
        super().__init__()
        self.model = model
        self.utterance = utterance
        self.graph = model.graph
        self.params = model.params
        self.learn = learn
        self.chunkinesses = []
        self.memory = deque(maxlen=self.params['MEMORY_SIZE'])

        self.log = model.log
        self.log.debug('')
        self.log.debug('PARSING: %s', utterance)

        utterance = iter(utterance)

        # Fill up memory before trying to chunk.
        while len(self.memory) < self.params['MEMORY_SIZE']:
            self.log.debug('memory = %s', self.memory)
            token = next(utterance, None)
            if token is None:
                self.log.debug('Break early.')
                break  # less than MEMORY_SIZE tokens in utterance
            self.shift(token)
            self.update_weights(position=-1)


        # Chunk and shift until we run out of tokens, at which point we
        # keep chunking until we reduce the utterance to one chunk or memory is empty.
        self.log.debug('Begin chunking.')
        while self.memory:
            self.log.debug('memory = %s', self.memory)
            # Chunk.
            chunk_idx = self.try_to_chunk()
            if chunk_idx is None:
                # Couldn't make a chunk; remove the oldest node 
                # to make room for a new one.
                oldest = self.memory.popleft()
                self.append(oldest)
                self.log.debug('dropped %s', oldest)
            else:
                # Made a chunk; update weights to adjacent nodes.
                self.update_weights(position=chunk_idx)

            # Shift.
            token = next(utterance, None)
            if token is not None:
                self.shift(token)
                self.update_weights(position=-1)

    def score(self, cost='chunkiness'):

        transitions = [self.model.chunkiness(n1, n2)
                       for n1, n2 in utils.neighbors(self)]

        transitions = (np.array(transitions) + .001).clip(0, 1)  # smoothing
        return np.prod(transitions) ** (1/len(self.utterance))

    def shift(self, token) -> None:
        """Adds a token to memory.

        This method can only be called when there is an empty slot in
        memory. If token has never been seen, this method creates a new
        node in the graph for it.
        """
        assert len(self.memory) < self.memory.maxlen
        self.log.debug('shift: %s', token)

        try:
            node = self.graph[token]
        except KeyError:  # a new token
            node = self.model.graph.create_node(token)
            if self.learn:
                self.graph.add(node)

        self.memory.append(node)

    def update_weights(self, position=None) -> None:
        """Strengthens the connection between every adjacent pair of nodes in memory.

        Additionally, we increase the connection between nodes that are in a 
        chunk in memory. For a pair ([the big], dog) we increase the weight of 
        the forward edge, [the big] -> dog, and the backward edge, dog -> [the big],
        as well as the forward edge, the -> big, and backward edge, big -> the.
        """
        if not self.learn:
            return

        bump_factor = self.params['LEARNING_RATE']
        to_bump = self.adjacent(position)

        for node1, node2 in to_bump:
            self.log.debug('  bump %s -> %s', node1, node2)
            if node1.id_string in self.graph and node2.id_string in self.graph:
                node1.bump_edge(node2, 'ftp', bump_factor)
                node2.bump_edge(node1, 'btp', bump_factor)

    def try_to_chunk(self) -> None:
        """Attempts to combine two Nodes in memory into one Node.

        Returns True for success, False for failure.
        """

        if len(self.memory) == 1:
            # We can't create a chunk when there's only one node left.
            # This can only happen while processing the tail, so we
            # must be done processing
            self.log.debug('done parsing')
            return None

        # Consider chunking all adjacent nodes in memory, except
        # boundary markers.
        chunkable =(n for n in self.memory if n.id_string != 'ø')
        pairs = list(utils.neighbors(chunkable))
        if not pairs:
            return

        chunkinesses = [self.model.chunkiness(node1, node2)
                        for node1, node2 in pairs]
        
        best_idx = np.argmax(chunkinesses)
        best_chunkiness = chunkinesses[best_idx]

        # See if the best pair already forms a chunk in the graph.
        chunk = self.model.get_chunk(*pairs[best_idx], create=False)
        # If the node doesn't exist, but chunkiness exceeds the 
        # threshold, create the chunk and add it to the graph
        if not chunk and best_chunkiness > self.params['CHUNK_THRESHOLD']:
            chunk = self.model.get_chunk(*pairs[best_idx], create=True, add=self.learn)

        if chunk:
            # Replace the two nodes in memory with the single chunk
            best_chunk = self.model.get_chunk(*pairs[best_idx], create=True)
            self.memory[best_idx] = best_chunk
            del self.memory[best_idx+1]
            self.chunkinesses.append(best_chunkiness)
            self.log.debug('create chunk: %s', best_chunk)
            return best_idx

        else:
            self.log.debug('no chunk created')
            return None

    def adjacent(self, idx):
        """Yields nodes adjacent to the given index in memory."""
        depth = self.params['DEPTH']
        idx = idx % len(self.memory)
        node = self.memory[idx]
        left = idx - 1
        if left >= 0:
            other_node = self.memory[left]
            # Yield all children on the right side.
            for _ in range(depth + 1):
                yield (other_node, node)
                other_node = other_node.child2
                if not other_node: break

        right = idx + 1
        if right < len(self.memory):
            other_node = self.memory[right]
            for _ in range(depth + 1):
                yield (node, other_node)
                other_node = other_node.child1
                if not other_node: break

    def __repr__(self):
        return super().__repr__().replace(',', ' |')[1:-1]

    def __str__(self):
        return self.__repr__()



def test_parse():
    import numila
    model = numila.Numila()
    #self.log.handlers[0].setLevel('DEBUG')
    #self.log.setLevel('DEBUG')
    model.parse('the dog ate')
    model.parse('the dog ate a steak')
    model.parse('I know the dog ate a steak')

if __name__ == '__main__':
    test_parse()
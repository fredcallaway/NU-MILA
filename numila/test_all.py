import pytest
from numila import *
from vectors import *

@pytest.fixture()
def graph():
    graph = Numila(DIM=1000, PERCENT_NON_ZERO=.01, CHUNK_THRESHOLD=0.5)
    with open('corpora/test.txt') as corpus:
                for i, s in enumerate(corpus.read().splitlines()):
                    graph.parse_utterance(s)  
    return graph

def test_parsing(graph):
    spanish = 'los gatos son grasos'
    parse = graph.parse_utterance(spanish)
    assert str(parse) == '[# los gatos son grasos #]'

def test_speaking(graph):
    words = 'the hill ate my cookie'.split()
    utterance = str(graph.speak(words))
    assert all(w in utterance for w in words)
    no_brackets = utterance.replace('[', '').replace(']', '')
    assert len(no_brackets.split()) == len(words)


@pytest.fixture()
def vector_model():
    return VectorModel(1000, .01, 'addition')

def test_vector_model(vector_model):
    vectors = [vector_model.sparse() for _ in range(5000)]
    assert(all(vec.shape == (vector_model.dim,) for vec in vectors))

    num_nonzero = vector_model.dim * vector_model.nonzero
    num_nonzeros = [len(np.nonzero(vec)[0]) for vec in vectors]
    assert(all(n == num_nonzero for n in num_nonzeros))

if __name__ == '__main__':
    pytest.main()
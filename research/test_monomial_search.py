"""Independent exact checks of the monomial search's sign-space reduction."""
import random
import unittest

from monomial_search import (all_clifford, all_pauli, basis_insert,
                             commutator_layer, in_span, inverse)


def signed_mul(g, h):
    f, s = g
    p, t = h
    return (tuple(f[p[x]] for x in range(len(f))),
            sum((((t >> x) ^ (s >> p[x])) & 1) << x for x in range(len(f))))


def signed_inverse(g):
    f, s = g
    p = inverse(f)
    return p, sum(((s >> p[x]) & 1) << x for x in range(len(f)))


def pauli(n, a, b):
    return (tuple(x ^ a for x in range(1 << n)),
            sum(((x & b).bit_count() % 2) << x for x in range(1 << n)))


def naive_layer(states, n):
    out = set()
    for g in states:
        for a in range(1 << n):
            for b in range(1 << n):
                p = pauli(n, a, b)
                out.add(signed_mul(signed_mul(signed_mul(g, p), signed_inverse(g)),
                                   signed_inverse(p)))
    return out


def collect_spaces(states):
    out = {}
    for f, s in states:
        basis_insert(out.setdefault(f, {}), s)
    return out


class ExactSearchChecks(unittest.TestCase):
    def test_sign_space_propagation_covers_every_pauli(self):
        n = 3
        f = tuple(x ^ ((((x >> 0) & 1) & ((x >> 1) & 1)) << 2)
                  for x in range(1 << n))
        naive = {(f, 0)}
        merged = {f: {}}
        for _ in range(2):
            naive = naive_layer(naive, n)
            merged = commutator_layer(merged, n, 10000)
            reference = collect_spaces(naive)
            self.assertEqual(reference.keys(), merged.keys())
            for f in merged:
                self.assertEqual(len(reference[f]), len(merged[f]))
                self.assertTrue(all(in_span(reference[f], s) for s in merged[f].values()))
                self.assertTrue(all(in_span(merged[f], s) for s in reference[f].values()))
        self.assertTrue(all_pauli(merged, n))

    def test_clifford_shortcut_matches_definition(self):
        n = 3
        random.seed(176)
        candidates = [pauli(n, a, b) for a in range(8) for b in range(8)]
        # CZ and an affine permutation with a quadratic sign are positive cases.
        identity = tuple(range(8))
        quadratic = sum((((x >> 0) & 1) & ((x >> 1) & 1)) << x for x in range(8))
        candidates.append((identity, quadratic))
        candidates.append((tuple(x ^ (((x >> 0) & 1) << 2) for x in range(8)), quadratic))
        for _ in range(20):
            f = list(range(8))
            random.shuffle(f)
            candidates.append((tuple(f), random.randrange(1 << 8)))
        for g in candidates:
            reference = all_pauli(collect_spaces(naive_layer({g}, n)), n)
            shortcut = all_clifford(collect_spaces({g}), n)
            self.assertEqual(reference, shortcut)


if __name__ == '__main__':
    unittest.main()

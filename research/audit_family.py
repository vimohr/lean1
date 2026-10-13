"""Exact audits of the universal family's finite permutation identities.

Coordinate identities are checked for s=1,...,10. They are computations,
not substitutes for the universal proof and not Lean certificates.
"""
import json
from pathlib import Path


def composition(f, g):
    return tuple(f[x] for x in g)


def inverse(f):
    out = [0] * len(f)
    for x, y in enumerate(f):
        out[y] = x
    return tuple(out)


def power(f, k):
    out = tuple(range(len(f)))
    while k:
        if k & 1:
            out = composition(f, out)
        f = composition(f, f)
        k >>= 1
    return out


def maps(s):
    q = 1 << s
    a, r, b, w = [], [], [], []
    for row in range(2):
        for z in range(q):
            a.append(q + z if row == 0 else (-z) % q)
            r.append((1 - z) % q if row == 0 else q + ((-z) % q))
            b.append(q + ((-z) % q) if row == 0 else (z + 1) % q)
            w.append(row * q + ((z + 1) % q))
    return tuple(a), tuple(r), tuple(b), tuple(w)


def blocks_mul(f, g):
    return tuple(composition(a, b) for a, b in zip(f, g))


def blocks_inverse(f):
    return tuple(inverse(a) for a in f)


def control_commutator(f, mask):
    return blocks_mul(f, tuple(inverse(f[z ^ mask]) for z in range(4)))


def audit(s):
    q = 1 << s
    a, r, b, w = maps(s)
    identity = tuple(range(2 * q))
    assert composition(r, a) == b
    assert power(a, 4) == identity
    assert power(r, 2) == identity
    assert power(b, 4) == identity
    assert power(b, 2) != identity
    d = composition(inverse(a), composition(r, a))
    assert composition(r, d) == w
    assert power(w, q) == identity
    assert power(w, q // 2) != identity
    # Controls are indexed 2*outer + inner. V=diag(I,I,A,B).
    f = (identity, identity, a, b)
    f = control_commutator(f, 2)
    assert f == (inverse(a), inverse(b), a, b)
    f = control_commutator(f, 1)
    assert f == (d, d, r, r)
    for j in range(1, s + 1):
        f = control_commutator(f, 2)
        exponent = 1 << (j - 1)
        assert f == (power(inverse(w), exponent),) * 2 + (power(w, exponent),) * 2
    assert len(set(f)) == 1
    assert f[0] == power(w, q // 2) != identity
    return {'s': s, 'q': q, 'data_qubits': 2*q,
            'target_qubits': 2*q + 1, 'controlled_qubits': 2*q + 2,
            'target_periodicity': 2, 'controlled_level_lower_bound': s + 4,
            'commutator_depth': s + 2, 'all_coordinate_and_block_checks': True}


if __name__ == '__main__':
    results = [audit(s) for s in range(1, 11)]
    path = Path(__file__).with_name('family_audit.json')
    path.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))

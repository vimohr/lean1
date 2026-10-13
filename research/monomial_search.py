"""Exact Clifford-hierarchy search for real monomial gates.

This is an independent integer-arithmetic experiment, not a Lean certificate.
A gate maps |x> to (-1)^s(x) |f(x)>. All 4^n Paulis are covered:
their scalar phases cancel in commutators, and their Z masks are propagated
as a linear space of Boolean functions. Merging sign spaces is exact because
the sign update is linear and the terminal Pauli condition is a subspace.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def inverse(f):
    out = [0] * len(f)
    for x, y in enumerate(f):
        out[y] = x
    return tuple(out)


def compose(f, g):
    return tuple(f[g[x]] for x in range(len(f)))


def basis_insert(basis, v):
    while v:
        p = v.bit_length() - 1
        if p in basis:
            v ^= basis[p]
        else:
            basis[p] = v
            return


def affine_sign_basis(n):
    d = 1 << n
    basis = {}
    basis_insert(basis, (1 << d) - 1)
    for j in range(n):
        basis_insert(basis, sum(((x >> j) & 1) << x for x in range(d)))
    return basis


def in_span(basis, v):
    while v:
        p = v.bit_length() - 1
        if p not in basis:
            return False
        v ^= basis[p]
    return True


def all_pauli(states, n):
    affine = affine_sign_basis(n)
    for f, basis in states.items():
        shift = f[0]
        if any(y != x ^ shift for x, y in enumerate(f)):
            return False
        if any(not in_span(affine, s) for s in basis.values()):
            return False
    return True


def sign_degree(s, n):
    """Boolean algebraic degree, computed by the exact Mobius transform."""
    coefficients = [(s >> x) & 1 for x in range(1 << n)]
    for j in range(n):
        for x in range(1 << n):
            if (x >> j) & 1:
                coefficients[x] ^= coefficients[x ^ (1 << j)]
    return max((x.bit_count() for x, c in enumerate(coefficients) if c), default=0)


def all_clifford(states, n):
    for f, basis in states.items():
        linear = [f[1 << j] ^ f[0] for j in range(n)]
        for x, y in enumerate(f):
            predicted = f[0]
            for j in range(n):
                if (x >> j) & 1:
                    predicted ^= linear[j]
            if predicted != y:
                return False
        if any(sign_degree(s, n) > 2 for s in basis.values()):
            return False
    return True


def commutator_layer(states, n, limit):
    d = 1 << n
    out = {}
    for f, sbasis in states.items():
        inv = inverse(f)
        for a in range(d):
            z = tuple(inv[x ^ a] for x in range(d))
            g = tuple(f[z[x] ^ a] for x in range(d))
            target = out.setdefault(g, {})
            for s in sbasis.values():
                v = sum((((s >> z[x]) ^ (s >> (z[x] ^ a))) & 1) << x
                        for x in range(d))
                basis_insert(target, v)
            for j in range(n):
                v = sum((((z[x] ^ x ^ a) >> j) & 1) << x for x in range(d))
                basis_insert(target, v)
        if len(out) > limit:
            raise RuntimeError(f"State limit {limit} exceeded")
    return out


def hrt(rank):
    n = (1 << rank) - 1
    gates = []
    for target in range(1, n + 1):
        for a in range(1, target):
            b = target - a
            if a < b and not (a & b):
                gates.append((a - 1, b - 1, target - 1))
    return n, gates


def circuit_perm(n, gates):
    out = []
    for x in range(1 << n):
        y = x
        for a, b, c in gates:
            y ^= (((y >> a) & 1) & ((y >> b) & 1)) << c
        out.append(y)
    return tuple(out)


def controlled(f):
    d = len(f)
    return tuple(range(d)) + tuple(d + y for y in f)


def position_permutation_gate(position_map):
    n = len(position_map)
    out = []
    for x in range(1 << n):
        y = 0
        for j, image in enumerate(position_map):
            y |= ((x >> j) & 1) << image
        out.append(y)
    return tuple(out)


def affine_family(s):
    q = 1 << s
    ndata = 2 * q
    amap = []
    bmap = []
    for row in range(2):
        for z in range(q):
            amap.append(q + z if row == 0 else (-z) % q)
            bmap.append(q + ((-z) % q) if row == 0 else (z + 1) % q)
    a = position_permutation_gate(amap)
    b = position_permutation_gate(bmap)
    d = 1 << ndata
    return ndata + 1, a + tuple(d + y for y in b)


def periodicity(f):
    for m in range(20):
        shift = f[0]
        if all(y == x ^ shift for x, y in enumerate(f)):
            return m
        f = compose(f, f)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rank', type=int, default=3)
    parser.add_argument('--family', choices=['hrt', 'affine'], default='hrt')
    parser.add_argument('--s', type=int, default=1)
    parser.add_argument('--control', action='store_true')
    parser.add_argument('--max-level', type=int, default=12)
    parser.add_argument('--state-limit', type=int, default=20000)
    args = parser.parse_args()
    if args.family == 'hrt':
        n, gates = hrt(args.rank)
        if n > 9:
            raise ValueError('Truth-table experiment limited to nine target qubits')
        f = circuit_perm(n, gates)
    else:
        if args.s > 2:
            raise ValueError('Full truth-table experiment only supports s <= 2')
        n, f = affine_family(args.s)
    m = periodicity(f)
    if args.control:
        f = controlled(f)
        n += 1
    states = {f: {}}
    log = {'family': args.family, 'rank': args.rank, 's': args.s,
           'control': args.control, 'qubits': n,
           'periodicity_target': m, 'layers': []}
    start = time.monotonic()
    for level in range(1, args.max_level + 1):
        terminal = all_pauli(states, n)
        clifford = all_clifford(states, n)
        entry = {'depth': level - 1, 'permutations': len(states),
                 'largest_sign_dimension': max(map(len, states.values())),
                 'all_pauli': terminal, 'all_clifford': clifford,
                 'seconds': time.monotonic() - start}
        log['layers'].append(entry)
        print(json.dumps(entry), flush=True)
        if terminal:
            log['exact_level'] = level
            break
        if clifford:
            log['exact_level'] = level + 1
            break
        states = commutator_layer(states, n, args.state_limit)
    tag = f'hrt_rank{args.rank}' if args.family == 'hrt' else f'affine_s{args.s}'
    destination = Path(__file__).with_name(f'{tag}_control{int(args.control)}.json')
    destination.write_text(json.dumps(log, indent=2) + '\n')


if __name__ == '__main__':
    main()

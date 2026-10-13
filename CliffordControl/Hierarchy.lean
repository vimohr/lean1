import Mathlib

/-!
# Abstract Clifford hierarchy and commutator arguments

UNCOMPILED SOURCE: no Lean executable was available when this file was written.
The declarations below are proposed proofs, not a claim of Lean certification.
There are no placeholders for proofs and no additional axioms in this file.

For a subgroup `P` of a group `G`, `hierarchy P 0` is `P`. Thus index `n`
corresponds to the usual Clifford-hierarchy level `n + 1` when `G` is the
unitary group and `P` is the Pauli subgroup with its discrete phases.

The results do not require `P` to be abelian or nilpotent. They use only that
`P` is a subgroup. The quantum-specific bridge (unitaries, the Pauli subgroup,
and the permutation/controlled-gate construction) is not formalized here.

The final theorem uses an explicitly supplied central filtration. In an
application to a finite 2-group containing the Paulis, obtaining that filtration
from finite-group nilpotence remains a separate, unformalized bridge.
-/

namespace CliffordControl

universe u

variable {G : Type u} [Group G]

/-- The group-theoretic Clifford hierarchy; index zero is the Pauli subgroup. -/
def hierarchy (P : Subgroup G) : ℕ → Set G
  | 0 => (P : Set G)
  | n + 1 => {g | ∀ p : G, p ∈ P → g * p * g⁻¹ ∈ hierarchy P n}

/-- Membership in some finite hierarchy level. -/
def inFiniteHierarchy (P : Subgroup G) (g : G) : Prop :=
  ∃ n : ℕ, g ∈ hierarchy P n

@[simp]
theorem mem_hierarchy_zero (P : Subgroup G) (g : G) :
    g ∈ hierarchy P 0 ↔ g ∈ P := Iff.rfl

@[simp]
theorem mem_hierarchy_succ (P : Subgroup G) (n : ℕ) (g : G) :
    g ∈ hierarchy P (n + 1) ↔
      ∀ p : G, p ∈ P → g * p * g⁻¹ ∈ hierarchy P n := Iff.rfl

/-- The hierarchy is nested. -/
theorem hierarchy_mem_succ (P : Subgroup G) {n : ℕ} {g : G}
    (hg : g ∈ hierarchy P n) : g ∈ hierarchy P (n + 1) := by
  induction n generalizing g with
  | zero =>
      change ∀ p : G, p ∈ P → g * p * g⁻¹ ∈ P
      intro p hp
      exact P.mul_mem (P.mul_mem hg hp) (P.inv_mem hg)
  | succ n ih =>
      change ∀ p : G, p ∈ P → g * p * g⁻¹ ∈ hierarchy P (n + 1)
      intro p hp
      exact ih (hg p hp)

/-- Membership persists after any finite increase of the index. -/
theorem hierarchy_mem_add (P : Subgroup G) {n : ℕ} {g : G}
    (hg : g ∈ hierarchy P n) (r : ℕ) : g ∈ hierarchy P (n + r) := by
  induction r with
  | zero => simpa using hg
  | succ r ih =>
      simpa only [Nat.add_assoc] using hierarchy_mem_succ P ih

/-- Right multiplication by a Pauli preserves membership at every level. -/
theorem hierarchy_mul_right (P : Subgroup G) {n : ℕ} {g q : G}
    (hg : g ∈ hierarchy P n) (hq : q ∈ P) :
    g * q ∈ hierarchy P n := by
  cases n with
  | zero => exact P.mul_mem hg hq
  | succ n =>
      change ∀ p : G, p ∈ P → (g * q) * p * (g * q)⁻¹ ∈ hierarchy P n
      intro p hp
      have hqp : q * p * q⁻¹ ∈ P :=
        P.mul_mem (P.mul_mem hq hp) (P.inv_mem hq)
      have himage : g * (q * p * q⁻¹) * g⁻¹ ∈ hierarchy P n := hg _ hqp
      have heq : (g * q) * p * (g * q)⁻¹ = g * (q * p * q⁻¹) * g⁻¹ := by
        group
      rw [heq]
      exact himage

/-- Right multiplication by a Pauli leaves level membership unchanged. -/
theorem hierarchy_mul_right_iff (P : Subgroup G) {n : ℕ} {g q : G}
    (hq : q ∈ P) :
    g * q ∈ hierarchy P n ↔ g ∈ hierarchy P n := by
  constructor
  · intro h
    have hcancel := hierarchy_mul_right P h (P.inv_mem hq)
    simpa [mul_assoc] using hcancel
  · intro h
    exact hierarchy_mul_right P h hq

/-- Conjugation by a Pauli preserves every level. -/
theorem hierarchy_conj (P : Subgroup G) {n : ℕ} {g q : G}
    (hg : g ∈ hierarchy P n) (hq : q ∈ P) :
    q * g * q⁻¹ ∈ hierarchy P n := by
  induction n generalizing g q with
  | zero => exact P.mul_mem (P.mul_mem hq hg) (P.inv_mem hq)
  | succ n ih =>
      change ∀ p : G, p ∈ P →
        (q * g * q⁻¹) * p * (q * g * q⁻¹)⁻¹ ∈ hierarchy P n
      intro p hp
      have hqp : q⁻¹ * p * q ∈ P :=
        P.mul_mem (P.mul_mem (P.inv_mem hq) hp) hq
      have himage : g * (q⁻¹ * p * q) * g⁻¹ ∈ hierarchy P n := hg _ hqp
      have hconj : q * (g * (q⁻¹ * p * q) * g⁻¹) * q⁻¹ ∈ hierarchy P n :=
        ih himage hq
      have heq : (q * g * q⁻¹) * p * (q * g * q⁻¹)⁻¹ =
          q * (g * (q⁻¹ * p * q) * g⁻¹) * q⁻¹ := by
        group
      rw [heq]
      exact hconj

/-- Left multiplication by a Pauli also preserves every level. -/
theorem hierarchy_mul_left (P : Subgroup G) {n : ℕ} {g q : G}
    (hg : g ∈ hierarchy P n) (hq : q ∈ P) :
    q * g ∈ hierarchy P n := by
  have h := hierarchy_mul_right P (hierarchy_conj P hg hq) hq
  simpa [mul_assoc] using h

/-- Our commutator convention is `[g,p] = g p g⁻¹ p⁻¹`. -/
def pauliCommutator (g p : G) : G := g * p * g⁻¹ * p⁻¹

/-- One Pauli commutator lowers the hierarchy index by one. -/
theorem pauliCommutator_mem (P : Subgroup G) {n : ℕ} {g p : G}
    (hg : g ∈ hierarchy P (n + 1)) (hp : p ∈ P) :
    pauliCommutator g p ∈ hierarchy P n := by
  have himage : g * p * g⁻¹ ∈ hierarchy P n := hg p hp
  exact hierarchy_mul_right P himage (P.inv_mem hp)

/-- The manuscript's derivative convention: `δ_p(g) = p g p⁻¹ g⁻¹`. -/
def pauliDerivative (g p : G) : G := p * g * p⁻¹ * g⁻¹

/-- The opposite commutator convention also lowers the index by one. -/
theorem pauliDerivative_mem (P : Subgroup G) {n : ℕ} {g p : G}
    (hg : g ∈ hierarchy P (n + 1)) (hp : p ∈ P) :
    pauliDerivative g p ∈ hierarchy P n := by
  have himage : g * p⁻¹ * g⁻¹ ∈ hierarchy P n := hg _ (P.inv_mem hp)
  have h := hierarchy_mul_left P himage hp
  simpa only [pauliDerivative, mul_assoc] using h

/-- Iteration in list order: first commute with the head, then with the tail. -/
def iteratedPauliCommutator (g : G) : List G → G
  | [] => g
  | p :: ps => iteratedPauliCommutator (pauliCommutator g p) ps

/-- A list of `r` Pauli commutators lowers the hierarchy index by `r`. -/
theorem iteratedPauliCommutator_mem (P : Subgroup G) {ps : List G}
    {n : ℕ} {g : G} (hg : g ∈ hierarchy P (ps.length + n))
    (hps : ∀ p : G, p ∈ ps → p ∈ P) :
    iteratedPauliCommutator g ps ∈ hierarchy P n := by
  induction ps generalizing g with
  | nil => simpa [iteratedPauliCommutator] using hg
  | cons p ps ih =>
      have hp : p ∈ P := hps p (by simp)
      have htail : ∀ q : G, q ∈ ps → q ∈ P := by
        intro q hq
        exact hps q (by simp [hq])
      have hg' : g ∈ hierarchy P ((ps.length + n) + 1) := by
        simpa only [List.length_cons, Nat.add_assoc, Nat.add_comm, Nat.add_left_comm]
          using hg
      have hcomm : pauliCommutator g p ∈ hierarchy P (ps.length + n) :=
        pauliCommutator_mem P hg' hp
      exact ih hcomm htail

/-- A non-Pauli iterated commutator rules out the corresponding level.

For the conventional hierarchy this excludes level `ps.length + 1`.
-/
theorem not_mem_hierarchy_of_iteratedPauliCommutator (P : Subgroup G)
    {ps : List G} {g : G}
    (hps : ∀ p : G, p ∈ ps → p ∈ P)
    (hnot : iteratedPauliCommutator g ps ∉ P) :
    g ∉ hierarchy P ps.length := by
  intro hg
  have hg' : g ∈ hierarchy P (ps.length + 0) := by simpa using hg
  have hfinal := iteratedPauliCommutator_mem P (n := 0) hg' hps
  exact hnot hfinal

/-- Iterated derivatives in the manuscript's commutator convention. -/
def iteratedPauliDerivative (g : G) : List G → G
  | [] => g
  | p :: ps => iteratedPauliDerivative (pauliDerivative g p) ps

/-- List descent for the manuscript's derivative convention. -/
theorem iteratedPauliDerivative_mem (P : Subgroup G) {ps : List G}
    {n : ℕ} {g : G} (hg : g ∈ hierarchy P (ps.length + n))
    (hps : ∀ p : G, p ∈ ps → p ∈ P) :
    iteratedPauliDerivative g ps ∈ hierarchy P n := by
  induction ps generalizing g with
  | nil => simpa [iteratedPauliDerivative] using hg
  | cons p ps ih =>
      have hp : p ∈ P := hps p (by simp)
      have htail : ∀ q : G, q ∈ ps → q ∈ P := by
        intro q hq
        exact hps q (by simp [hq])
      have hg' : g ∈ hierarchy P ((ps.length + n) + 1) := by
        simpa only [List.length_cons, Nat.add_assoc, Nat.add_comm, Nat.add_left_comm]
          using hg
      have hcomm : pauliDerivative g p ∈ hierarchy P (ps.length + n) :=
        pauliDerivative_mem P hg' hp
      exact ih hcomm htail

/-- The obstruction theorem, with exactly the manuscript's derivative. -/
theorem not_mem_hierarchy_of_iteratedPauliDerivative (P : Subgroup G)
    {ps : List G} {g : G}
    (hps : ∀ p : G, p ∈ ps → p ∈ P)
    (hnot : iteratedPauliDerivative g ps ∉ P) :
    g ∉ hierarchy P ps.length := by
  intro hg
  have hg' : g ∈ hierarchy P (ps.length + 0) := by simpa using hg
  have hfinal := iteratedPauliDerivative_mem P (n := 0) hg' hps
  exact hnot hfinal

/-- An explicit central filtration places its stages in the hierarchy.

The initial stage must lie in `P`; each next stage must have all its
commutators with `P` in the preceding stage. These are group-theoretic
conditions on subgroups, with no hierarchy-membership assumptions.
-/
theorem central_filtration_mem_hierarchy (P : Subgroup G)
    (F : ℕ → Subgroup G) (hbase : F 0 ≤ P)
    (hstep : ∀ n : ℕ, ∀ g : G, g ∈ F (n + 1) →
      ∀ p : G, p ∈ P → pauliCommutator g p ∈ F n)
    {n : ℕ} {g : G} (hg : g ∈ F n) :
    g ∈ hierarchy P n := by
  induction n generalizing g with
  | zero => exact hbase hg
  | succ n ih =>
      change ∀ p : G, p ∈ P → g * p * g⁻¹ ∈ hierarchy P n
      intro p hp
      have hcomm : pauliCommutator g p ∈ hierarchy P n :=
        ih (hstep n g hg p hp)
      have hproduct := hierarchy_mul_right P hcomm hp
      simpa [pauliCommutator, mul_assoc] using hproduct

/-- A subgroup covered by a supplied central filtration lies in finite levels.

For a nilpotent subgroup `H` containing `P`, one may supply an upper central
series in the ambient group: `F 0 = ⊥`, `H ≤ F height`, and `hstep`. Constructing
that series from mathlib's nilpotence API is deliberately not claimed here.
-/
theorem subgroup_inFiniteHierarchy_of_central_filtration (P H : Subgroup G)
    (F : ℕ → Subgroup G) (height : ℕ) (hbase : F 0 ≤ P)
    (hstep : ∀ n : ℕ, ∀ g : G, g ∈ F (n + 1) →
      ∀ p : G, p ∈ P → pauliCommutator g p ∈ F n)
    (hcover : H ≤ F height) {g : G} (hg : g ∈ H) :
    inFiniteHierarchy P g := by
  exact ⟨height, central_filtration_mem_hierarchy P F hbase hstep (hcover hg)⟩

end CliffordControl

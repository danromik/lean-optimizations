import Mathlib

/-! Exercises for the lazily loaded parts: proof bodies, unexposed definition bodies,
private names, the interpreter (tactics implemented in Mathlib), `#eval`, `decide`. -/

-- theorem bodies (`.olean.private`)
#print Nat.add_comm
#print Real.exp_pos
#print Finset.sum_range_succ
#print axioms Real.exp_pos

-- unexposed definition bodies (exported as axioms, kernel/`whnf` need the real definition)
example : Finset.card (Finset.range 5) = 5 := by decide
example : (List.range 10).sum = 45 := by decide
example : Nat.gcd 12 18 = 6 := by decide
example : Finset.card (Finset.range 5) = 5 := by simp
example : Finset.card (Finset.range 5) = 5 := by unfold Finset.card Finset.range; rfl
example : Nat.factorial 5 = 120 := by unfold Nat.factorial; rfl
example (n : ℕ) : Nat.factorial (n + 1) = (n + 1) * Nat.factorial n := by rw [Nat.factorial_succ]

-- the interpreter: Mathlib tactics (IR of Mathlib modules), `#eval`, `norm_num`, `ring`, `omega`
example (a b : ℝ) (h : 0 < a) (hb : 0 < b) : 0 < a * b := by positivity
example (a b c : ℝ) : a * (b + c) = a * b + a * c := by ring
example (x y : ℝ) (h : x < y) : x ≤ y := by linarith
example : (2 : ℝ) ≤ 3 := by norm_num
example (n : ℕ) (h : 3 ≤ n) : 1 < n := by omega
example (s : Finset ℕ) : ∑ i ∈ Finset.range 0, (i : ℕ) = 0 := by simp
example : Real.sqrt 4 = 2 := by
  rw [show (4 : ℝ) = 2 ^ 2 by norm_num, Real.sqrt_sq (by norm_num)]
#eval (List.range 10).map (· ^ 2)
#eval Nat.gcd 1234 5678
#eval (Finset.range 10).sum id
#eval ∑ i ∈ Finset.range 101, i
#eval (2 : ℤ) ^ 100
#eval Nat.factorial 20
#eval (3 : ℚ) / 4 + 1 / 4

-- private declarations of imported modules are visible to a legacy root
-- (`_private.<Module>.0.<name>` names resolve through the private part)
open private implies_congr' heq_imp_of_eq_imp from Mathlib.Tactic.CongrExclamation
#check @implies_congr'
#print implies_congr'
#print axioms implies_congr'
#check @heq_imp_of_eq_imp

-- a theorem whose proof is `by simp`, proof term printed from the private part
theorem foo (n : ℕ) (h : 2 ≤ n) : 4 ≤ n ^ 2 := by nlinarith
#print foo
#print axioms foo

-- `exact?` walks `header.moduleData`
example (a b : ℕ) : a + b = b + a := by exact?
example (a : ℝ) (h : 0 < a) : 0 < a ^ 2 := by exact?

-- definitional unfolding in the kernel (`rfl` proofs through unexposed definitions)
example : ((fun x : ℕ => x + 1) 2) = 3 := rfl
example : Nat.choose 5 2 = 10 := by decide
example : Nat.choose 5 2 = 10 := rfl
example : (Polynomial.X : Polynomial ℤ).natDegree = 1 := Polynomial.natDegree_X

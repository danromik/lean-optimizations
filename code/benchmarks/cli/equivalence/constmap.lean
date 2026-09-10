import Mathlib
#check Nat.add_comm
#check @Real.exp_pos
#print Nat.add_comm
#print Real.exp_pos
#print axioms Real.exp_pos
#print Finset.sum_range_succ
#check (inferInstance : Field ℝ)
open Nat in
example (n : ℕ) : n + 0 = n := by exact?
example (a b : ℝ) (h : 0 < a) (hb : 0 < b) : 0 < a * b := by exact?
example (x : ℝ) : Real.exp x > 0 := by simp [Real.exp_pos]
example (s : Finset ℕ) (f : ℕ → ℕ) : ∑ i ∈ Finset.range 0, f i = 0 := by simp
example (a b c : ℝ) : a * (b + c) = a * b + a * c := by ring
example (x y : ℝ) (h : x < y) : x ≤ y := by linarith
theorem foo (n : ℕ) (h : 2 ≤ n) : 4 ≤ n ^ 2 := by nlinarith
#print axioms foo
example : (2 : ℝ) ≤ 3 := by norm_num
#check Matrix.det_mul
#print Matrix.det_mul

import Mathlib

open Polynomial

theorem natDegree_example : (X ^ 2 + 1 : ℚ[X]).natDegree = 2 := by compute_degree!

theorem degree_cubic : (X ^ 3 - 2 * X + 1 : ℤ[X]).degree = 3 := by compute_degree!

theorem eval_example : (X ^ 2 + 1 : ℚ[X]).eval 2 = 5 := by simp; norm_num

theorem volume_Icc' : MeasureTheory.volume (Set.Icc (0 : ℝ) 1) = 1 := by simp

theorem volume_Ioo' (a b : ℝ) (h : a ≤ b) :
    MeasureTheory.volume (Set.Ioo a b) = ENNReal.ofReal (b - a) := by simp

theorem integral_id' : ∫ x in (0 : ℝ)..1, x = 1 / 2 := by
  simp

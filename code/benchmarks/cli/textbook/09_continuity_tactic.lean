import Mathlib

/-- Same statement as in 08 but via the (aesop-based) `continuity` tactic. -/
theorem poly_continuous' : Continuous (fun x : ℝ => x ^ 2 + 3 * x + 1) := by
  continuity

theorem exp_sin_continuous : Continuous (fun x : ℝ => Real.exp (Real.sin x) * Real.cos x) := by
  continuity

theorem exp_sin_continuous' : Continuous (fun x : ℝ => Real.exp (Real.sin x) * Real.cos x) := by
  fun_prop

theorem diff_poly : Differentiable ℝ (fun x : ℝ => x ^ 3 - 2 * x + Real.exp x) := by
  fun_prop

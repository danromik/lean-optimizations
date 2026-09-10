import Mathlib

open Filter Topology

/-- `1/(n+1) → 0`. -/
theorem one_div_tendsto_zero : Tendsto (fun n : ℕ => (1 : ℝ) / (n + 1)) atTop (𝓝 0) :=
  tendsto_one_div_add_atTop_nhds_zero_nat

/-- Squeeze: a sequence bounded by `1/(n+1)` in absolute value tends to `0`. -/
theorem squeeze_to_zero (a : ℕ → ℝ) (h : ∀ n, |a n| ≤ 1 / (n + 1)) : Tendsto a atTop (𝓝 0) := by
  have h0 : Tendsto (fun n : ℕ => (1 : ℝ) / (n + 1)) atTop (𝓝 0) := tendsto_one_div_add_atTop_nhds_zero_nat
  refine squeeze_zero_norm (fun n => ?_) h0
  simpa using h n

/-- A polynomial is continuous. -/
theorem poly_continuous : Continuous (fun x : ℝ => x ^ 2 + 3 * x + 1) := by
  fun_prop

/-- The limit of a polynomial at a point. -/
theorem poly_tendsto : Tendsto (fun x : ℝ => x ^ 2 + 3 * x + 1) (𝓝 2) (𝓝 11) := by
  have := poly_continuous.tendsto 2
  norm_num at this
  exact this

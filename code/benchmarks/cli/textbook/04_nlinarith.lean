import Mathlib

/-- AM–GM for two variables, via `nlinarith` with a hint. -/
theorem two_mul_le_sq_add_sq (a b : ℝ) : 2 * a * b ≤ a ^ 2 + b ^ 2 := by
  nlinarith [sq_nonneg (a - b)]

theorem sq_sum_le (a b : ℝ) : (a + b) ^ 2 ≤ 2 * (a ^ 2 + b ^ 2) := by
  nlinarith [sq_nonneg (a - b)]

theorem cauchy_two (a b c d : ℝ) : (a * c + b * d) ^ 2 ≤ (a ^ 2 + b ^ 2) * (c ^ 2 + d ^ 2) := by
  nlinarith [sq_nonneg (a * d - b * c)]

theorem pos_of_sq (x : ℝ) (hx : 0 < x) : 0 < x ^ 3 + x := by
  nlinarith [sq_nonneg x, pow_pos hx 3]

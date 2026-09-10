import Mathlib

theorem avg_lt (x y : ℝ) (h : x < y) : (x + y) / 2 < y := by linarith

theorem chain_bound (a b c : ℝ) (h1 : a ≤ b) (h2 : b ≤ c) (h3 : 0 ≤ a) : a + b ≤ 2 * c := by
  linarith

theorem abs_bound (x : ℝ) (h1 : -3 ≤ x) (h2 : x ≤ 3) : |x| ≤ 3 := by
  rw [abs_le]
  constructor <;> linarith

theorem three_vars (x y z : ℚ) (h1 : x + y + z = 6) (h2 : x - y = 1) (h3 : y - z = 1) : x = 3 := by
  linarith

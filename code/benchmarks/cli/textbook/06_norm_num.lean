import Mathlib

theorem two_pow_ten : (2 : ℚ) ^ 10 = 1024 := by norm_num

theorem prime_97 : Nat.Prime 97 := by norm_num

theorem not_prime_91 : ¬ Nat.Prime 91 := by norm_num

theorem mod_calc : (123456789 : ℕ) % 7 = 1 := by norm_num

theorem real_calc : ((3 : ℝ) / 4) ^ 2 + 7 / 16 = 1 := by norm_num

theorem gcd_calc : Nat.gcd 462 1071 = 21 := by norm_num

theorem sqrt_bound : Real.sqrt 2 < 3 / 2 := by
  rw [Real.sqrt_lt' (by norm_num)]
  norm_num

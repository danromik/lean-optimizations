import Mathlib
set_option maxHeartbeats 1000000
-- Tactics whose behaviour depends on an eagerly built table that the tactic index image defers:
-- norm_num extensions, positivity extensions, norm_cast/push_cast sets, gcongr, fun_prop,
-- to_additive translations, grind, aesop, the `ext` lemma table, match-equation tables.

-- norm_num
example : (127 : ℕ).Prime := by norm_num
example : (2 : ℝ) ^ 10 = 1024 := by norm_num
example : (3 : ℚ) / 4 + 1 / 4 = 1 := by norm_num
example : Nat.gcd 42 56 = 14 := by norm_num
example : (10 : ℤ) ∣ 1230 := by norm_num
example : ¬ (91 : ℕ).Prime := by norm_num
example : (5 : ℝ) < 2 ^ 3 := by norm_num
example (x : ℝ) : x ^ 2 - 2 * x + 1 = (x - 1) ^ 2 := by ring

-- positivity
example (x : ℝ) (hx : 0 < x) : 0 < x ^ 2 + x := by positivity
example (x : ℝ) : 0 ≤ x ^ 2 := by positivity
example (x : ℝ) (hx : 0 < x) : 0 < Real.exp x := by positivity
example (x : ℝ) (hx : 0 < x) : 0 < Real.sqrt x + 1 := by positivity
example (n : ℕ) : 0 < n ! := by positivity
example (x y : ℝ) (hx : 0 < x) (hy : 0 < y) : 0 < x * y / (x + y) := by positivity

-- norm_cast / push_cast
example (m n : ℕ) : ((m + n : ℕ) : ℤ) = (m : ℤ) + n := by push_cast; ring
example (m n : ℕ) (h : (m : ℤ) = n) : m = n := by exact_mod_cast h
example (n : ℕ) : ((n ^ 2 : ℕ) : ℝ) = (n : ℝ) ^ 2 := by push_cast; ring
example (q : ℚ) : ((q : ℝ) : ℂ) = ((q : ℂ)) := by push_cast; ring

-- gcongr
example (a b c d : ℝ) (h1 : a ≤ b) (h2 : c ≤ d) : a + c ≤ b + d := by gcongr
example (x y : ℝ) (hx : 0 ≤ x) (h : x ≤ y) : x ^ 3 ≤ y ^ 3 := by gcongr

-- ext lemma table
example (s t : Set ℕ) (h : ∀ x, x ∈ s ↔ x ∈ t) : s = t := by ext x; exact h x
example (f g : ℕ → ℕ) (h : ∀ x, f x = g x) : f = g := by ext x; exact h x

-- to_additive: the translation table maps multiplicative names to additive ones
#check @mul_comm
#check @add_comm
#check @one_mul
#check @zero_add
#check @inv_inv
#check @neg_neg
example (G : Type) [AddCommGroup G] (a b : G) : a + b = b + a := add_comm a b

-- fun_prop
example : Continuous (fun x : ℝ => x ^ 2 + Real.sin x) := by fun_prop
example : Continuous (fun x : ℝ => Real.exp (Real.cos x)) := by fun_prop
example (f : ℝ → ℝ) (hf : Continuous f) : Continuous (fun x => f x + 1) := by fun_prop

-- aesop / grind / decide / omega
example (p q : Prop) (hp : p) (hq : q) : p ∧ q := by aesop
example (s : Set ℕ) (x : ℕ) (h : x ∈ s) : s.Nonempty := by aesop
example (n : ℕ) (h : n < 3) : n = 0 ∨ n = 1 ∨ n = 2 := by omega
example : (7 : ℕ) % 3 = 1 := by decide

-- linarith / nlinarith (preprocessing uses norm_num extensions)
example (x y : ℝ) (h1 : x + y = 3) (h2 : x - y = 1) : x = 2 := by linarith
example (x : ℝ) (h : 0 ≤ x) : 0 ≤ x ^ 2 + 2 * x := by nlinarith

-- unfolding / match equations (Match.Extension)
def f2 : ℕ → ℕ
  | 0 => 1
  | n + 1 => 2 * f2 n
example : f2 3 = 8 := by simp [f2]
#print f2
#check @f2.eq_def

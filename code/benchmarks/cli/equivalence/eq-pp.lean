import Mathlib
-- Pretty-printing: `delabAttribute` and `appUnexpanderAttribute` are deferred by the tactic
-- index image, so any change would show up as different notation in the printed terms.
open Finset BigOperators in
#check fun (f : ℕ → ℝ) (s : Finset ℕ) => ∑ i ∈ s, f i
open Finset in
#check fun (f : ℕ → ℝ) (s : Finset ℕ) => ∏ i ∈ s, f i
#check fun (f : ℝ → ℝ) => ∫ x, f x
#check fun (s : Set ℕ) => sᶜ
#check fun (x : ℝ) => |x|
#check fun (n : ℕ) => n !
#check fun (a b : ℕ) => a ∣ b
#check fun (M : Matrix (Fin 2) (Fin 2) ℝ) => M.det
#check fun (x : ℝ) => Real.sqrt x
#check fun (f : ℕ →+* ℕ) => f
#check fun (G H : Type) [Group G] [Group H] (f : G →* H) => f
#check fun (V W : Type) [AddCommGroup V] [AddCommGroup W] [Module ℝ V] [Module ℝ W] (f : V →ₗ[ℝ] W) => f
#check fun (α : Type) (s : Set α) => sᶜ ∩ s
#check fun (p : ℕ × ℕ) => p.1 + p.2
#check fun (f : ℕ ≃ ℕ) => f.symm
#check fun (l : List ℕ) => l.map (· + 1)
#check (⟨1, 2⟩ : ℕ × ℕ)
#check fun (x : ℝ) => Real.exp x * Real.log x
#print Nat.add
#print List.map
#print Finset.sum
#print axioms Nat.add_comm
#print axioms Real.add_pow_le_pow_mul_pow_of_sq_le_sq

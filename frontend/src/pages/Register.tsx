import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { AuthLayout, FormMessage } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import { useRegister, useSession } from "@/hooks/useSession";
import { applyApiErrors } from "@/lib/forms";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// The API's rule (PRD F1): 10 to 128 characters. The API stays the authority; this only saves a round trip.
const PASSWORD_MIN = 10;
const PASSWORD_MAX = 128;
const characters = (value: string) => [...value].length;

interface RegisterValues {
  email: string;
  password: string;
}

export default function Register() {
  useDocumentTitle("Create your account");
  const navigate = useNavigate();
  const session = useSession();
  const registerAccount = useRegister();
  const [formError, setFormError] = useState<string>();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<RegisterValues>({ defaultValues: { email: "", password: "" } });

  if (session.data) return <Navigate to="/app" replace />;

  const onSubmit = handleSubmit((values) => {
    setFormError(undefined);
    const email = values.email;
    registerAccount.mutate(
      { email, password: values.password },
      {
        // Registering does not sign you in: the next step is the sign-in screen.
        onSuccess: () => navigate("/login", { state: { registered: true } }),
        onError: (error) => {
          if (error instanceof ApiError && error.code === "email_taken") {
            setError("email", { type: "server", message: "This email is already registered." });
            return;
          }
          setFormError(applyApiErrors(error, setError, ["email", "password"]));
        },
      },
    );
  });

  return (
    <AuthLayout
      title="Create your account"
      intro="Short links and their click counts, in one place."
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="font-bold text-route underline underline-offset-2">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {formError && <FormMessage tone="error">{formError}</FormMessage>}
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          error={errors.email?.message}
          {...register("email", { required: "Enter your email." })}
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="new-password"
          hint={`${PASSWORD_MIN} to ${PASSWORD_MAX} characters.`}
          error={errors.password?.message}
          {...register("password", {
            required: "Choose a password.",
            validate: (value) =>
              characters(value) < PASSWORD_MIN
                ? `Use at least ${PASSWORD_MIN} characters.`
                : characters(value) > PASSWORD_MAX
                  ? `Use at most ${PASSWORD_MAX} characters.`
                  : true,
          })}
        />
        <Button type="submit" size="lg" className="w-full" disabled={registerAccount.isPending}>
          {registerAccount.isPending ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthLayout>
  );
}

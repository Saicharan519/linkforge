import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useLocation } from "react-router-dom";
import { AuthLayout, FormMessage } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import { useLogin, useSession } from "@/hooks/useSession";
import { applyApiErrors, safeReturnPath } from "@/lib/forms";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

interface LoginValues {
  email: string;
  password: string;
}

export default function Login() {
  useDocumentTitle("Sign in");
  const location = useLocation();
  const session = useSession();
  const login = useLogin();
  const [formError, setFormError] = useState<string>();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<LoginValues>({ defaultValues: { email: "", password: "" } });

  // Already signed in (or just signed in): go where the user was headed.
  if (session.data) return <Navigate to={safeReturnPath(location.state)} replace />;

  const notice =
    typeof location.state === "object" && location.state !== null && "registered" in location.state;

  const onSubmit = handleSubmit((values) => {
    setFormError(undefined);
    login.mutate(
      { email: values.email, password: values.password },
      { onError: (error) => setFormError(applyApiErrors(error, setError, ["email", "password"])) },
    );
  });

  return (
    <AuthLayout
      title="Sign in"
      intro="Welcome back."
      footer={
        <>
          New to LinkForge?{" "}
          <Link to="/register" className="font-bold text-route underline underline-offset-2">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {notice && !formError && <FormMessage tone="ok">Account created. Sign in to continue.</FormMessage>}
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
          autoComplete="current-password"
          error={errors.password?.message}
          {...register("password", { required: "Enter your password." })}
        />
        <Button type="submit" size="lg" className="w-full" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthLayout>
  );
}

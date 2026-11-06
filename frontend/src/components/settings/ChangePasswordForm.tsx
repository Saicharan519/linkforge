import { useState } from "react";
import { useForm } from "react-hook-form";
import { ApiError } from "@/api/client";
import { FormMessage } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import { useChangePassword } from "@/hooks/useApiKeys";
import { applyApiErrors } from "@/lib/forms";

// Same rule as register (PRD F1): 10 to 128 characters, counted in characters.
const PASSWORD_MIN = 10;
const PASSWORD_MAX = 128;
const characters = (value: string) => [...value].length;

interface Values {
  old_password: string;
  new_password: string;
}

export function ChangePasswordForm() {
  const change = useChangePassword();
  const [formError, setFormError] = useState<string>();
  const [done, setDone] = useState(false);
  const {
    register,
    handleSubmit,
    setError,
    reset,
    formState: { errors },
  } = useForm<Values>({ defaultValues: { old_password: "", new_password: "" } });

  const onSubmit = handleSubmit((values) => {
    setFormError(undefined);
    setDone(false);
    change.mutate(values, {
      onSuccess: () => {
        reset();
        setDone(true);
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 422 && error.fieldMessage("old_password")) {
          setError("old_password", { type: "server", message: "Your current password is wrong." });
          return;
        }
        if (error instanceof ApiError && error.code === "unavailable") {
          // The API changes the password first, then signs the other sessions out; this error can
          // mean the first part already happened.
          setFormError("LinkForge could not finish this change. Your password may already be the new one: try signing in with it.");
          return;
        }
        setFormError(applyApiErrors(error, setError, ["old_password", "new_password"]));
      },
    });
  });

  return (
    <section aria-labelledby="password-title" className="space-y-4">
      <div>
        <h2 id="password-title" className="text-2xl font-extrabold tracking-tight">
          Password
        </h2>
        <p className="text-muted-foreground">Changing it signs out your other browsers. API keys keep working.</p>
      </div>
      <form onSubmit={onSubmit} noValidate className="plate max-w-md space-y-4 p-5">
        {done && <FormMessage tone="ok">Password changed. Your other sessions were signed out.</FormMessage>}
        {formError && <FormMessage tone="error">{formError}</FormMessage>}
        <TextField
          label="Current password"
          type="password"
          autoComplete="current-password"
          error={errors.old_password?.message}
          {...register("old_password", { required: "Enter your current password." })}
        />
        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          hint={`${PASSWORD_MIN} to ${PASSWORD_MAX} characters.`}
          error={errors.new_password?.message}
          {...register("new_password", {
            required: "Choose a new password.",
            validate: (value) =>
              characters(value) < PASSWORD_MIN
                ? `Use at least ${PASSWORD_MIN} characters.`
                : characters(value) > PASSWORD_MAX
                  ? `Use at most ${PASSWORD_MAX} characters.`
                  : true,
          })}
        />
        <Button type="submit" disabled={change.isPending}>
          {change.isPending ? "Changing…" : "Change password"}
        </Button>
      </form>
    </section>
  );
}

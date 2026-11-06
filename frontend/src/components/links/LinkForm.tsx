import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { ApiError } from "@/api/client";
import type { Link } from "@/api/types";
import { FormMessage } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/text-field";
import { useCreateLink, useUpdateLink } from "@/hooks/useLinks";
import { applyApiErrors } from "@/lib/forms";
import {
  buildCreateBody,
  buildPatchBody,
  emptyLinkValues,
  valuesFromLink,
  type LinkFormValues,
} from "@/lib/linkBody";

type Field = keyof LinkFormValues;
const CREATE_FIELDS: Field[] = ["destination_url", "alias", "title", "expires_at"];
const EDIT_FIELDS: Field[] = ["destination_url", "title", "expires_at"];

/** Create (no `link`) or edit (`link`) form. The alias is chosen once and cannot be changed. */
export function LinkForm({
  link,
  onDone,
  onCancel,
}: {
  link?: Link;
  onDone: (saved: Link) => void;
  onCancel: () => void;
}) {
  const create = useCreateLink();
  const update = useUpdateLink();
  const [formError, setFormError] = useState<string>();
  const {
    register,
    handleSubmit,
    setError,
    control,
    formState: { errors },
  } = useForm<LinkFormValues>({ defaultValues: link ? valuesFromLink(link) : emptyLinkValues() });

  const pending = create.isPending || update.isPending;
  // Editing with nothing changed has nothing to save.
  const current = useWatch({ control });
  const unchanged = link ? Object.keys(buildPatchBody(link, { ...valuesFromLink(link), ...current })).length === 0 : false;

  const onSubmit = handleSubmit((values) => {
    setFormError(undefined);
    const onError = (error: Error) => {
      if (error instanceof ApiError && error.code === "alias_taken") {
        setError("alias", { type: "server", message: "This alias is already in use." });
        return;
      }
      setFormError(applyApiErrors(error, setError, link ? EDIT_FIELDS : CREATE_FIELDS));
    };
    if (link) {
      update.mutate({ id: link.id, body: buildPatchBody(link, values) }, { onSuccess: onDone, onError });
    } else {
      create.mutate(buildCreateBody(values), { onSuccess: onDone, onError });
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {formError && <FormMessage tone="error">{formError}</FormMessage>}
      <TextField
        label="Destination URL"
        type="url"
        inputMode="url"
        autoComplete="off"
        placeholder="https://example.com/a/long/page"
        error={errors.destination_url?.message}
        {...register("destination_url", { required: "Enter a destination URL." })}
      />
      {!link && (
        <TextField
          label="Custom alias (optional)"
          autoComplete="off"
          hint="3 to 32 letters, digits, _ or -. Leave empty for a generated code."
          error={errors.alias?.message}
          {...register("alias")}
        />
      )}
      <TextField
        label="Title (optional)"
        autoComplete="off"
        hint="Only you see it. Up to 200 characters."
        error={errors.title?.message}
        {...register("title", { maxLength: { value: 200, message: "Use at most 200 characters." } })}
      />
      <TextField
        label="Expires (optional)"
        type="datetime-local"
        hint="Your local time. After this the link answers 410 Gone. Empty = never."
        error={errors.expires_at?.message}
        {...register("expires_at")}
      />
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || unchanged}>
          {pending ? "Saving…" : link ? "Save changes" : "Create link"}
        </Button>
      </div>
    </form>
  );
}

/** Temporary body of a screen that a later P4 task builds. Removed screen by screen. */
export function PageStub({ title, task }: { title: string; task: string }) {
  return (
    <section className="mx-auto max-w-2xl p-6">
      <h1 className="text-3xl font-extrabold tracking-tight">{title}</h1>
      <p className="mt-2 text-muted-foreground">This screen is built in task {task}.</p>
    </section>
  );
}

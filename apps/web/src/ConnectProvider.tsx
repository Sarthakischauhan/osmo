import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** One entry of symphony-server's `GET /providers` (never carries a secret). */
export const providerInfoSchema = z.object({
  id: z.string(),
  label: z.string(),
  authKinds: z.array(z.enum(["api_key", "oauth"])),
  connected: z.boolean(),
});
export type ProviderInfo = z.infer<typeof providerInfoSchema>;
export const providersResponseSchema = z.object({ providers: z.array(providerInfoSchema) });

const keyFormSchema = z.object({ key: z.string().trim().min(1, "Paste an API key") });
type KeyForm = z.infer<typeof keyFormSchema>;
const errorSchema = z.object({ detail: z.string() });

export interface ConnectProviderDialogProps {
  provider: ProviderInfo | undefined;
  onOpenChange: (open: boolean) => void;
  /** The server stored a validated key: refresh the registry. */
  onConnected: (provider: ProviderInfo) => void;
  /** Starts the server's OAuth flow; defaults to navigating to it. */
  startOAuth?: (url: string) => void;
}

/** "Connect provider": an API key (validated by the server before it is stored) or, where offered, OAuth. */
export function ConnectProviderDialog({ provider, onOpenChange, onConnected, startOAuth = (url) => window.location.assign(url) }: ConnectProviderDialogProps) {
  const form = useForm<KeyForm>({ resolver: zodResolver(keyFormSchema), defaultValues: { key: "" } });
  const oauth = provider?.authKinds.includes("oauth") === true;

  const submit = async ({ key }: KeyForm) => {
    if (!provider) return;
    const response = await fetch(`/providers/${encodeURIComponent(provider.id)}/key`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).catch(() => undefined);
    if (response?.ok) {
      form.reset();
      toast.success(`${provider.label} connected`);
      onConnected(provider);
      return;
    }
    const detail = response ? errorSchema.safeParse(await response.json().catch(() => undefined)) : undefined;
    form.setError("key", { message: detail?.success ? detail.data.detail : `Could not reach the server to connect ${provider.label}` });
  };

  return (
    <Dialog
      open={provider !== undefined}
      onOpenChange={(open) => {
        if (!open) form.reset();
        onOpenChange(open);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Connect {provider?.label}</DialogTitle>
          <DialogDescription>Its models unlock once the server holds a working credential. Keys stay on the server.</DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="api_key">
          <TabsList aria-label="How to connect">
            <TabsTrigger value="api_key">API key</TabsTrigger>
            <TabsTrigger value="oauth">OAuth</TabsTrigger>
          </TabsList>
          <TabsContent value="api_key" className="pt-4">
            <Form {...form}>
              <form className="grid gap-4" onSubmit={form.handleSubmit(submit)}>
                <FormField
                  control={form.control}
                  name="key"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-white/80">{provider?.label} API key</FormLabel>
                      <FormControl>
                        <Input type="password" autoComplete="off" spellCheck={false} placeholder="sk-…" {...field} />
                      </FormControl>
                      <FormDescription className="text-white/40">Checked with one request to {provider?.label}, then stored encrypted.</FormDescription>
                      <FormMessage className="text-white/80" />
                    </FormItem>
                  )}
                />
                <Button type="submit" disabled={form.formState.isSubmitting} className="justify-self-end">
                  {form.formState.isSubmitting ? "Checking…" : "Connect"}
                </Button>
              </form>
            </Form>
          </TabsContent>
          <TabsContent value="oauth" className="grid gap-4 pt-4">
            {oauth ? (
              <>
                <p className="m-0 text-sm text-white/60">Sign in to {provider?.label} and approve Osmo; it hands the server a key for your account.</p>
                <Button className="justify-self-end" onClick={() => provider && startOAuth(`/providers/${encodeURIComponent(provider.id)}/oauth/start`)}>
                  Continue with {provider?.label}
                </Button>
              </>
            ) : (
              <p className="m-0 text-sm text-white/60">{provider?.label} does not offer OAuth for API access. Create a key in its console and use the API key tab.</p>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

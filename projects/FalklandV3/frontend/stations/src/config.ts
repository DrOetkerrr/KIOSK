export function getApiKey(): string | undefined {
  const key = import.meta.env.VITE_FALKLAND_API_KEY;
  if (typeof key === "string" && key.trim().length > 0) {
    return key;
  }
  return undefined;
}

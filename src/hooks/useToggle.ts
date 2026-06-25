import { useCallback, useState } from "react";

export function useToggle(defaultValue = false) {
  const [enabled, setEnabled] = useState(defaultValue);
  const toggle = useCallback(() => setEnabled((value) => !value), []);

  return [enabled, toggle] as const;
}

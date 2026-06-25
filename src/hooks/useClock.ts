import { useEffect, useState } from "react";

export function useClock() {
  const [time, setTime] = useState(() => new Date().toTimeString().slice(0, 8));

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setTime(new Date().toTimeString().slice(0, 8));
    }, 1000);

    return () => window.clearInterval(intervalId);
  }, []);

  return time;
}

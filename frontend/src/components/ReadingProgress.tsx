import { useEffect, useState, type RefObject } from "react";
import { useReducedMotion } from "framer-motion";

export function ReadingProgress({
  target,
}: {
  target: RefObject<HTMLDivElement | null>;
}) {
  const [progress, setProgress] = useState(0);
  const reduced = useReducedMotion();
  useEffect(() => {
    const element = target.current;
    if (!element) return;
    let frame = 0;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      const viewport = window.innerHeight;
      const maximum = Math.max(
        0,
        document.documentElement.scrollHeight - viewport,
      );
      const top = rect.top + window.scrollY;
      const start = Math.min(maximum, Math.max(0, top - viewport * 0.35));
      const end = Math.min(
        maximum,
        Math.max(start, top + rect.height - viewport * 0.8),
      );
      const value =
        rect.bottom <= viewport + 1
          ? 1
          : end <= start
            ? 0
            : (window.scrollY - start) / (end - start);
      setProgress(Math.min(1, Math.max(0, value)));
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    observer.observe(document.body);
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    measure();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [target]);
  if (reduced) return null;
  return (
    <svg
      className="reading-progress"
      data-progress={Math.round(progress * 100)}
      viewBox="0 0 12 1000"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d="M6 3 V997" className="reading-track" />
      <path
        d="M6 3 V997"
        pathLength="1"
        strokeDasharray="1"
        strokeDashoffset={1 - progress}
      />
      <circle cx="6" cy={3 + progress * 994} r="3" />
    </svg>
  );
}

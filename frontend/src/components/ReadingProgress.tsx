// Scroll-linked path adapted for a reading rail, inspired by Skiper UI skiper19.
import {
  motion,
  useScroll,
  useReducedMotion,
  useTransform,
} from "framer-motion";
import type { RefObject } from "react";
export function ReadingProgress({
  target,
}: {
  target: RefObject<HTMLDivElement | null>;
}) {
  const { scrollYProgress } = useScroll({
    target,
    offset: ["start center", "end center"],
  });
  const y = useTransform(scrollYProgress, [0, 1], [0, 1000]);
  const reduced = useReducedMotion();
  if (reduced) return null;
  return (
    <svg
      className="reading-progress"
      viewBox="0 0 12 1000"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d="M6 0 V1000" className="reading-track" />
      <motion.path d="M6 0 V1000" style={{ pathLength: scrollYProgress }} />
      <motion.circle cx="6" r="3" style={{ cy: y }} />
    </svg>
  );
}

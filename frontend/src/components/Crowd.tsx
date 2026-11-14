// Sprite artwork: Open Peeps. Canvas concept: Skiper UI 39 / Zadvorsky.
import { useEffect, useRef, useState } from "react";
import { ScrollTrail } from "./ScrollTrail";
import { useReducedMotion } from "framer-motion";

export function Crowd() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [paused, setPaused] = useState(false);
  const timeRef = useRef(0);
  const reduced = useReducedMotion();
  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    let frame = 0,
      visible = false,
      loaded = false,
      last = 0,
      elapsed = timeRef.current;
    let width = 0,
      height = 0;
    const image = new Image();
    const draw = () => {
      if (!loaded) return;
      context.clearRect(0, 0, width, height);
      const count = width < 600 ? 8 : 14;
      const sw = image.naturalWidth / 15,
        sh = image.naturalHeight / 7;
      for (let index = 0; index < count; index++) {
        const sprite = (index * 7 + 3) % 105;
        const h = 100 + (index % 4) * 12;
        const w = (sw / sh) * h;
        const direction = index % 2 ? -1 : 1;
        const distance =
          ((index * width) / count + elapsed * (30 + (index % 5) * 2)) %
          (width + w * 2);
        const x = direction > 0 ? distance - w : width - distance + w;
        const y = height - h - 3 + Math.sin(elapsed * 7 + index) * 2;
        context.save();
        context.translate(x + (direction < 0 ? w : 0), y);
        context.scale(direction, 1);
        context.drawImage(
          image,
          (sprite % 15) * sw,
          Math.floor(sprite / 15) * sh,
          sw,
          sh,
          0,
          0,
          w,
          h,
        );
        context.restore();
      }
    };
    const tick = (time: number) => {
      if (time - last >= 33) {
        elapsed += last ? Math.min((time - last) / 1000, 0.1) : 0;
        timeRef.current = elapsed;
        last = time;
        draw();
      }
      frame = requestAnimationFrame(tick);
    };
    const sync = () => {
      cancelAnimationFrame(frame);
      last = 0;
      draw();
      if (loaded && visible && !document.hidden && !paused && !reduced)
        frame = requestAnimationFrame(tick);
    };
    const resize = () => {
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      const ratio = Math.min(devicePixelRatio || 1, 2);
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      sync();
    };
    image.onload = () => {
      loaded = true;
      resize();
    };
    image.src = "/motion/crowd.png";
    const observer = new IntersectionObserver((entries) => {
      visible = entries[0].isIntersecting;
      sync();
    });
    observer.observe(canvas);
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(canvas);
    document.addEventListener("visibilitychange", sync);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      resizeObserver.disconnect();
      image.onload = null;
      document.removeEventListener("visibilitychange", sync);
    };
  }, [paused, reduced]);
  return (
    <section className="crowd-section" aria-label="Learning crowd illustration">
      <ScrollTrail />
      <div className="crowd-heading">
        <p>Keep learning, at your own pace.</p>
        {!reduced && (
          <button className="text-button" onClick={() => setPaused(!paused)}>
            {paused ? "Resume animation" : "Pause animation"}
          </button>
        )}
      </div>
      <canvas className="crowd-canvas" ref={canvasRef} aria-hidden="true" />
      <a
        className="crowd-credit"
        href="https://www.openpeeps.com/"
        target="_blank"
        rel="noreferrer"
      >
        Illustrations by Open Peeps · Motion inspired by Skiper UI
      </a>
    </section>
  );
}

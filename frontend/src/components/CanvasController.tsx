import { useEffect, useRef, useState } from 'react';

interface CanvasControllerProps {
  frameIndex: number; // Decimal value, e.g., 115.4
}

export const CanvasController: React.FC<CanvasControllerProps> = ({ frameIndex }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [images, setImages] = useState<HTMLImageElement[]>([]);
  const [loaded, setLoaded] = useState(false);

  // Preload 300 frames.
  //
  // These are ~73 MB of JPEGs. Requesting and decoding them all at once
  // saturates the browser's image decoder, which delays first paint of
  // everything else on the page - it kept the Leaflet tiles on the researcher
  // dashboard blank for tens of seconds. Frames are therefore fetched at low
  // priority in small batches, and the render loop clamps to whatever has
  // arrived, so the animation fills in progressively instead of blocking.
  useEffect(() => {
    let cancelled = false;
    const totalFrames = 300;
    const batchSize = 12;

    const loadFrame = (i: number) =>
      new Promise<HTMLImageElement>((resolve, reject) => {
        const img = new Image();
        img.fetchPriority = 'low';
        img.src = `/frames/frame-${i.toString().padStart(3, '0')}.jpg`;

        // Decode before resolving to prevent GPU stalls
        img.decode()
          .then(() => resolve(img))
          .catch(() => {
            // Fallback for decoding errors or if testing locally without hardware decoding
            img.onload = () => resolve(img);
            img.onerror = reject;
          });
      });

    const preloadImages = async () => {
      try {
        const firstImage = await loadFrame(1);
        if (cancelled) return;
        setImages([firstImage]);
        setLoaded(true);

        const collected: HTMLImageElement[] = [firstImage];
        for (let start = 2; start <= totalFrames; start += batchSize) {
          const batch: Promise<HTMLImageElement>[] = [];
          for (let i = start; i < start + batchSize && i <= totalFrames; i++) {
            batch.push(loadFrame(i));
          }
          const done = await Promise.all(batch);
          if (cancelled) return;
          collected.push(...done);
          setImages([...collected]);
        }
      } catch (err) {
        console.error("Error preloading images:", err);
      }
    };

    preloadImages();
    return () => {
      cancelled = true;
    };
  }, []);

  // Render loop
  useEffect(() => {
    if (!loaded || images.length === 0 || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d', { alpha: false }); // alpha false for optimization
    if (!ctx) return;

    let animationFrameId: number;
    let lastRenderedFrameIndex = -1;

    const resizeCanvas = () => {
      const dpr = window.devicePixelRatio || 1;
      // Use client dimensions for canvas sizing
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      
      // Force an immediate redraw
      lastRenderedFrameIndex = -1;
    };

    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    const render = () => {
      // Only render if the frame changed (with some tolerance)
      if (Math.abs(lastRenderedFrameIndex - frameIndex) < 0.001) {
        animationFrameId = requestAnimationFrame(render);
        return;
      }
      
      lastRenderedFrameIndex = frameIndex;

      const currentIdx = Math.floor(frameIndex);
      
      const safeCurrentIdx = Math.min(Math.max(0, currentIdx), images.length - 1);
      const safeNextIdx = Math.min(currentIdx + 1, images.length - 1);
      
      const fraction = frameIndex - currentIdx;

      const imgCurrent = images[safeCurrentIdx];
      const imgNext = images[safeNextIdx];

      if (!imgCurrent) return;

      const { width, height } = canvas;
      
      // Calculate aspect ratio cover logic
      const drawImageCover = (img: HTMLImageElement, alpha: number) => {
        const imgRatio = img.width / img.height;
        const canvasRatio = width / height;
        let drawWidth, drawHeight, offsetX, offsetY;

        if (imgRatio > canvasRatio) {
          drawHeight = height;
          drawWidth = height * imgRatio;
          offsetX = (width - drawWidth) / 2;
          offsetY = 0;
        } else {
          drawWidth = width;
          drawHeight = width / imgRatio;
          offsetX = 0;
          offsetY = (height - drawHeight) / 2;
        }

        ctx.globalAlpha = alpha;
        ctx.drawImage(img, offsetX, offsetY, drawWidth, drawHeight);
      };

      // Base frame (no alpha)
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#090D16';
      ctx.fillRect(0, 0, width, height);

      drawImageCover(imgCurrent, 1);

      // Blend fractional frame
      if (fraction > 0 && safeCurrentIdx !== safeNextIdx && imgNext) {
        drawImageCover(imgNext, fraction);
      }

      animationFrameId = requestAnimationFrame(render);
    };

    animationFrameId = requestAnimationFrame(render);

    return () => {
      window.removeEventListener('resize', resizeCanvas);
      cancelAnimationFrame(animationFrameId);
    };
  }, [loaded, images, frameIndex]);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 w-[100vw] h-[100vh] -z-10 object-cover pointer-events-none will-change-transform"
      style={{ transform: 'translateZ(0)' }}
    />
  );
};

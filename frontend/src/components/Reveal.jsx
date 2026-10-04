import React, { useEffect, useRef, useState } from 'react';

/**
 * Fades/lifts its children in once they scroll into view, then leaves them
 * alone (no re-triggering on scroll-away, so the page never feels jumpy on
 * re-scroll). Freezes to "already visible" for prefers-reduced-motion.
 *
 * Usage: <Reveal><h2>...</h2></Reveal>  or  <Reveal as="span" delay={120}>
 */
export default function Reveal({ children, as: Tag = 'div', delay = 0, className = '', style, ...rest }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setVisible(true);
      return;
    }
    const node = ref.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
          }
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -8% 0px' }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      className={`reveal ${visible ? 'in-view' : ''} ${className}`}
      style={{ transitionDelay: visible ? `${delay}ms` : '0ms', ...style }}
      {...rest}
    >
      {children}
    </Tag>
  );
}

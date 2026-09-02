'use client';

/**
 * Last-resort boundary: catches errors in the root layout itself.
 *
 * `error.tsx` renders *inside* the layout, so it cannot help when the layout is
 * what failed. This one replaces the entire document, which is why it must
 * supply its own `<html>` and `<body>`.
 *
 * It also cannot rely on anything the app provides. No design-system component,
 * no Tailwind class, no theme token — if the layout failed, the stylesheet may
 * never have been applied. Everything here is inline style on purpose; a
 * beautifully-classed error screen that renders as unstyled white text on white
 * is the exact failure this file exists to prevent.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif', background: '#fff' }}>
        <div
          role="alert"
          style={{
            maxWidth: 640,
            margin: '12vh auto',
            padding: 24,
            border: '3px solid #b91c1c',
            borderRadius: 8,
            background: '#fef2f2',
            color: '#111',
          }}
        >
          <p
            style={{
              margin: 0,
              fontSize: 18,
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.03em',
              color: '#b91c1c',
            }}
          >
            Nightingale could not start
          </p>

          <p style={{ marginTop: 14, fontSize: 15, fontWeight: 500, lineHeight: 1.55 }}>
            The application failed to load. This is an interface fault — the patient
            record itself is stored on the server and is not affected.
          </p>

          <p style={{ marginTop: 10, fontSize: 15, lineHeight: 1.55, color: '#444' }}>
            Reload to try again. If it fails a second time, use another device and
            tell your IT contact — do not keep retrying while a patient is waiting.
          </p>

          <button
            onClick={reset}
            style={{
              marginTop: 18,
              padding: '10px 18px',
              fontSize: 15,
              fontWeight: 600,
              color: '#fff',
              background: '#b91c1c',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer',
            }}
          >
            Reload Page
          </button>

          {error.digest && (
            <p
              style={{
                marginTop: 18,
                paddingTop: 12,
                borderTop: '1px solid #ddd',
                fontFamily: 'monospace',
                fontSize: 12,
                color: '#666',
              }}
            >
              Reference for support: {error.digest}
            </p>
          )}
        </div>
      </body>
    </html>
  );
}

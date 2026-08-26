import type { Metadata } from 'next';
import { DM_Sans, Plus_Jakarta_Sans } from 'next/font/google';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from 'sonner';
import { NavigationProgress } from '@/components/layout/NavigationProgress';
import './globals.css';

const dmSans = DM_Sans({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-dm-sans',
  weight: ['300', '400', '500', '600', '700'],
});

const plusJakarta = Plus_Jakarta_Sans({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-plus-jakarta',
  weight: ['500', '600', '700', '800'],
});

export const metadata: Metadata = {
  title: 'Nightingale',
  description: 'A real-time collaborative patient note system with AI-powered insights',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${dmSans.variable} ${plusJakarta.variable}`}>
      <body>
        <NavigationProgress />
        <TooltipProvider delayDuration={200}>
          {children}
          <Toaster
            position="bottom-right"
            richColors
            toastOptions={{
              className: 'font-sans',
              style: {
                borderRadius: '12px',
              },
            }}
          />
        </TooltipProvider>
      </body>
    </html>
  );
}

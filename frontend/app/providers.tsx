"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, ReactNode } from "react";
import { IntlClientProvider } from "@/components/providers/IntlClientProvider";

interface ProvidersProps {
  children: ReactNode;
}

export function Providers({ children }: ProvidersProps): JSX.Element {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000, // 1 min - data considered fresh
            gcTime: 5 * 60 * 1000, // 5 min - keep unused data in cache
            refetchOnWindowFocus: false,
            retry: 1,
            refetchOnReconnect: true,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>
      <IntlClientProvider>{children}</IntlClientProvider>
    </QueryClientProvider>
  );
}

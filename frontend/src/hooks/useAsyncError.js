// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import { useCallback, useState } from "react";

/**
 * useAsyncError — surfaces asynchronous errors to the nearest React error boundary.
 *
 * React error boundaries only catch errors thrown during render. Errors thrown
 * in async callbacks (event handlers, useEffect) are silently swallowed.
 * This hook works around that by re-throwing them during the next render cycle.
 *
 * Usage:
 *   const throwAsync = useAsyncError();
 *
 *   async function load() {
 *     try {
 *       await fetchSomething();
 *     } catch (err) {
 *       throwAsync(err);   // will reach the nearest ErrorBoundary
 *     }
 *   }
 */
export function useAsyncError() {
  const [, setError] = useState(null);

  return useCallback((error) => {
    setError(() => {
      throw error;
    });
  }, []);
}

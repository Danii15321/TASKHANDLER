"use client";

import type { ReactNode } from "react";

import { ErrorMessage } from "@/components/error-message";

// requires : appelé par Next.js quand un segment lève une erreur ; `retry` relance son rendu.
// assigns  : rien.
// ensures  : affiche le message générique. L'erreur reçue n'est volontairement jamais lue : en
//            développement, elle contient le message d'origine, qui pourrait révéler un détail
//            interne s'il était affiché.
export default function ErrorPage({ retry }: { error: unknown; retry: () => void }): ReactNode {
  return <ErrorMessage onRetry={retry} />;
}

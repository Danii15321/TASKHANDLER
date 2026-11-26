"use client";

import type { ReactNode } from "react";

import { useSessionRestore } from "@/session/use-session";

// requires : placé une seule fois, dans la mise en page racine.
// assigns  : lance la restauration de la session au chargement de la page.
// ensures  : n'affiche rien.
export function SessionBootstrap(): ReactNode {
  useSessionRestore();
  return null;
}

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn-convention class combiner (same as apps/admin). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

import { Shield, Microscope, User as UserIcon } from "lucide-react";
import type { UserRole } from "@/lib/api";

export const roleColors: Record<UserRole, string> = {
  admin: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  researcher: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  viewer: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

export const roleIcons: Record<UserRole, React.ElementType> = {
  admin: Shield,
  researcher: Microscope,
  viewer: UserIcon,
};

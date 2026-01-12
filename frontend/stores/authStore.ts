import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api, User } from "@/lib/api";

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;

  login: (email: string, password: string) => Promise<void>;
  register: (email: string, name: string, password: string) => Promise<void>;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      isLoading: true,
      isAuthenticated: false,

      login: async (email: string, password: string) => {
        const response = await api.login(email, password);
        api.setToken(response.access_token);
        set({ user: response.user, isAuthenticated: true });
      },

      register: async (email: string, name: string, password: string) => {
        const response = await api.register({ email, name, password });
        api.setToken(response.access_token);
        set({ user: response.user, isAuthenticated: true });
      },

      logout: () => {
        api.setToken(null);
        set({ user: null, isAuthenticated: false });
      },

      checkAuth: async () => {
        set({ isLoading: true });
        try {
          const token = api.getToken();
          if (!token) {
            set({ isLoading: false, isAuthenticated: false });
            return;
          }
          const user = await api.getMe();
          set({ user, isAuthenticated: true, isLoading: false });
        } catch {
          api.setToken(null);
          set({ user: null, isAuthenticated: false, isLoading: false });
        }
      },
    }),
    {
      name: "auth-storage",
      partialize: (state) => ({ user: state.user }),
    }
  )
);

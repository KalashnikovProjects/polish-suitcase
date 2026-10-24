export interface User {
  id: number;
  email: string;
  created_at?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface ApiError {
  error: {
    code: string;
    message: string;
  };
}
export type AuthSession = {
  token_type: string;
  access_expires_in: number;
  refresh_expires_in: number;
};

export type Profile = {
  sub: string;
  role?: string;
  tenant?: string;
  scope?: string;
  iss?: string;
  aud?: string;
  typ?: string;
  exp?: number;
};

export type AdminUser = {
  username: string;
  role: string;
  tenant: string;
  scopes: string[];
};

export type ConversationSummary = {
  conversation_id: string;
  message_count: number;
  updated_at: string;
};

export type ConversationMessage = {
  message_id: string;
  message: string;
  created_at: string;
};

export type ConversationDetail = {
  conversation_id: string;
  tenant_id: string;
  user_id: string;
  messages: ConversationMessage[];
  updated_at: string;
};

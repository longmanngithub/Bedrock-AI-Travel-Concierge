import { cookies } from "next/headers";
import ClientPage from "./ClientPage.jsx";

export default async function Page() {
  const cookieStore = await cookies();
  const hasConversations = cookieStore.get("bedrock_has_conversations")?.value === "true";
  const conversationCount = parseInt(cookieStore.get("bedrock_conversation_count")?.value || "0", 10);
  const activeStructure = cookieStore.get("bedrock_active_conv_structure")?.value || "";

  return (
    <ClientPage
      initialHasConversations={hasConversations}
      initialConversationCount={conversationCount}
      initialActiveStructure={activeStructure}
    />
  );
}

import "../global.css"
import { Stack } from "expo-router"
import { StatusBar } from "expo-status-bar"
import { SafeAreaProvider } from "react-native-safe-area-context"

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" backgroundColor="#FAF9F6" />
      <Stack screenOptions={{ headerShown: false, animation: "slide_from_right" }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="language" />
        <Stack.Screen name="onboarding" />
        <Stack.Screen name="patient-info" />
        <Stack.Screen name="camera" />
        <Stack.Screen name="processing" />
        <Stack.Screen name="confirm" />
        <Stack.Screen name="categorize" />
        <Stack.Screen name="report" />
      </Stack>
    </SafeAreaProvider>
  )
}

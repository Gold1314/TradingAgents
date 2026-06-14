const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

// `markdown-it` (via react-native-markdown-display) requires Node's built-in
// `punycode`, which the React Native runtime does not provide. Resolve it to the
// userland `punycode` package instead. The trailing slash forces a node_modules
// lookup rather than the (removed) Node core module.
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  punycode: require.resolve('punycode/'),
};

module.exports = config;

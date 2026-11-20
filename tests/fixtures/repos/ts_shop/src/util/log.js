export function info(message) {
  console.log(message);
}

export default function warn(message, ...rest) {
  console.warn(message, ...rest);
}

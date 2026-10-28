cmake_minimum_required(VERSION 3.22 FATAL_ERROR)
project(NeuroBridgeAffectiveBridge LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(AFFECTIVE_SDK_SOURCE_DIR "" CACHE PATH "Checkout of AffectiveCloud-Algorithm-SDK")
if(NOT EXISTS "${AFFECTIVE_SDK_SOURCE_DIR}/cpp/package/CMakeLists.txt")
  message(FATAL_ERROR "Set AFFECTIVE_SDK_SOURCE_DIR to the locked AffectiveCloud-Algorithm-SDK checkout")
endif()

# The upstream package owns its source list and links its required header-only
# dependencies through CMake package targets.
add_subdirectory("${AFFECTIVE_SDK_SOURCE_DIR}/cpp/package" "affective-sdk")

add_executable(neurobridge_affective_bridge affective_bridge.cpp)
target_link_libraries(neurobridge_affective_bridge PRIVATE AffectiveAlgorithm)
set_target_properties(neurobridge_affective_bridge PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

option(NEUROBRIDGE_BUILD_SDK_TESTS "Build synthetic SDK behavior regression tests" OFF)
if(NEUROBRIDGE_BUILD_SDK_TESTS)
  enable_testing()
  add_executable(neurobridge_sdk_eeg_output_test ../../tests/native/test_sdk_eeg_output.cpp)
  target_include_directories(neurobridge_sdk_eeg_output_test PRIVATE "${AFFECTIVE_SDK_SOURCE_DIR}/cpp/package/include")
  target_link_libraries(neurobridge_sdk_eeg_output_test PRIVATE AffectiveAlgorithm)
  add_test(NAME sdk_eeg_output COMMAND neurobridge_sdk_eeg_output_test)
endif()

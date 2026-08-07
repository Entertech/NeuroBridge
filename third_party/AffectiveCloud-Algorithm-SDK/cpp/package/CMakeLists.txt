##################################################    Project     ##################################################
cmake_minimum_required(VERSION 3.22 FATAL_ERROR)
project               (AffectiveAlgorithm VERSION 2.0.0 LANGUAGES CXX)
list                  (APPEND CMAKE_MODULE_PATH ${PROJECT_SOURCE_DIR}/cmake)
set_property          (GLOBAL PROPERTY USE_FOLDERS ON)
set                   (CMAKE_CXX_STANDARD 17)
set                   (CMAKE_CXX_VISIBILITY_PRESET hidden)
set                   (CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${ADDITIONAL_CFLAGS} -g")
set                   (CMAKE_VISIBILITY_INLINES_HIDDEN 1)


if(MSVC)
  # Force to always compile with W4
  if(CMAKE_CXX_FLAGS MATCHES "/W[0-4]")
    string(REGEX REPLACE "/W[0-4]" "/W4" CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS}")
  else()
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /W4")
  endif()
elseif(CMAKE_COMPILER_IS_GNUCC OR CMAKE_COMPILER_IS_GNUCXX)
  # Update if necessary
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wno-long-long -pedantic")
endif()

##################################################    Options     ##################################################
option(BUILD_SHARED_LIBS "Build shared (dynamic) libraries." OFF)
option(BUILD_FOR_IOS "Build for iOS." OFF)
option(BUILD_FOR_ANDROID "Build for Android." OFF)

##################################################    Sources     ##################################################
file(GLOB_RECURSE PROJECT_SOURCES source/*.c source/*.cpp source/AC/*.cpp source/DP/*.cpp source/BASIC/TOOL/*.cpp source/BASIC/ENTERDSP/*.cpp source/BASIC/ENTERAFFECTION/*.cpp)
file(GLOB_RECURSE PROJECT_CMAKE_UTILS cmake/*.cmake)
file(GLOB_RECURSE PROJECT_MISC LIST_DIRECTORIES false *.bat *.gitignore *.md *.py *.sh *.txt)

set (PROJECT_FILES
        ${PROJECT_SOURCES}
        ${PROJECT_CMAKE_UTILS}
        ${PROJECT_MISC})


include            (assign_source_group)
assign_source_group(${PROJECT_FILES})

##################################################  Dependencies  ##################################################
include(import_library)

if (BUILD_SHARED_LIBS)
  if (BUILD_FOR_ANDROID)
    set(Eigen3_DIR "/opt/homebrew/Cellar/eigen/3.4.0_1/share/eigen3/cmake")
    find_package(Eigen3 REQUIRED)

    set(Boost_INCLUDE_DIR "/opt/homebrew/Cellar/boost/1.82.0_1/include")
    set(Boost_USE_STATIC_LIBS ON)
    set(Boost_USE_MULTITHREADED ON)
    set(Boost_USE_STATIC_RUNTIME OFF)
    find_package(Boost REQUIRED)
    if(Boost_FOUND)
      include_directories(${Boost_INCLUDE_DIRS})

      set(PROJECT_LIBRARIES  Eigen3::Eigen ${Boost_LIBRARIES})
    else ()
      set(PROJECT_LIBRARIES  Eigen3::Eigen)
    endif()

    set(CMAKE_SYSTEM_NAME Android)
    set(CMAKE_SYSTEM_VERSION 24)
    set(CMAKE_ANDROID_ARCH_ABI arm64-v8a)

    SET( ${PROJECT_NAME}_CURRENT 1 )
    SET( ${PROJECT_NAME}_REVISION 0 )
    SET( ${PROJECT_NAME}_AGE 0 )
    SET(VERSION "${${PROJECT_NAME}_CURRENT}.${${PROJECT_NAME}_REVISION}.${${PROJECT_NAME}_AGE}")


    include_directories("/opt/homebrew/Cellar/numcpp/2.11.0/include")
  endif ()
  add_library(${PROJECT_NAME} SHARED ${PROJECT_FILES})
else()
  if (BUILD_FOR_IOS)
    # set(CMAKE_SYSTEM_NAME iOS)
    # set(CMAKE_C_COMPILER  /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang)
    # set(CMAKE_CXX_COMPILER /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang++)
    # set(CMAKE_OSX_ARCHITECTURES arm64;x86_64)
    # set(CMAKE_XCODE_EFFECTIVE_PLATFORMS "-iphoneos;-iphonesimulator")
    set(Boost_INCLUDE_DIR "/opt/homebrew/Cellar/boost/1.86.0_2/include")
    set(NumCpp_DIR "/opt/homebrew/Cellar/numcpp/2.11.0/share/NumCpp/cmake")
    set(Eigen3_DIR "/opt/homebrew/Cellar/eigen/3.4.0_1/share/eigen3/cmake")

    set(Boost_NO_WARN_NEW_VERSIONS 1)
    find_package(NumCpp REQUIRED)
    find_package(Eigen3 CONFIG REQUIRED)

    set(PROJECT_LIBRARIES  Eigen3::Eigen NumCpp::NumCpp)
    add_library(${PROJECT_NAME} STATIC ${PROJECT_FILES})

  else ()
    find_package(Eigen3 CONFIG REQUIRED)
    find_package(NumCpp CONFIG REQUIRED)

    set(PROJECT_LIBRARIES    NumCpp::NumCpp Eigen3::Eigen )
    add_library(${PROJECT_NAME} STATIC ${PROJECT_FILES})
  endif ()

endif ()

##################################################    Targets     ##################################################

add_library(sub::libac ALIAS ${PROJECT_NAME})

target_include_directories(${PROJECT_NAME} PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/public)
target_include_directories(${PROJECT_NAME} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries     (${PROJECT_NAME} PUBLIC ${PROJECT_LIBRARIES})
set_target_properties     (${PROJECT_NAME} PROPERTIES LINKER_LANGUAGE CXX)
if(NOT BUILD_SHARED_LIBS)
  string               (TOUPPER ${PROJECT_NAME} PROJECT_NAME_UPPER)
  set_target_properties(${PROJECT_NAME} PROPERTIES COMPILE_FLAGS -D${PROJECT_NAME_UPPER}_STATIC)
endif()

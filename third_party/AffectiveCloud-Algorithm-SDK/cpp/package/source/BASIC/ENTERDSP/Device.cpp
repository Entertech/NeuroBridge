#include "Device.h"
#include <cmath>
namespace basic
{
    namespace dsp
    {
        double DeviceInfoFtV1::eegFs()
        {
            return 250.0;
        }

        double DeviceInfoFtV1::eegVoltageGain()
        {
            return 12.0;
        }

        int DeviceInfoFtV1::eegMinVal()
        {
            return 0;
        }

        int DeviceInfoFtV1::eegMaxVal()
        {
            return 16777215;
        }

        double DeviceInfoFtV1::eegMinUv()
        {
            return double(-2.4 * pow(10, 6));
        }

        double DeviceInfoFtV1::eegMaxUv()
        {
            return double(2.4 * pow(10, 6));
        }

        int DeviceInfoFtV1::eegDigits()
        {
            return 24;
        }

        double DeviceInfoFtV1::hrFs()
        {
            return 5;
        }

        double DeviceInfoFtV1::peFs() {
            return -1;
        }

        double DeviceInfoFtV1::prFs() {
            return -1;
        }

        double DeviceInfoFtV1::peMinMv() {
            return -1;
        }

        double DeviceInfoFtV1::peMaxMv() {
            return -1;
        }

        int DeviceInfoFtV1::peMinVal() {
            return -1;
        }

        int DeviceInfoFtV1::peMaxVal() {
            return -1;
        }

        //DeviceInfoEyeShade
        double DeviceInfoEyeShade::eegFs()
        {
            return 250.0;
        }

        double DeviceInfoEyeShade::eegVoltageGain()
        {
            return 12.0;
        }

        int DeviceInfoEyeShade::eegMinVal()
        {
            return 0;
        }

        int DeviceInfoEyeShade::eegMaxVal()
        {
            return 16777215;
        }

        double DeviceInfoEyeShade::eegMinUv()
        {
            return double(-2.4 * pow(10, 6));
        }

        double DeviceInfoEyeShade::eegMaxUv()
        {
            return double(2.4 * pow(10, 6));
        }

        int DeviceInfoEyeShade::eegDigits()
        {
            return 24;
        }

        double DeviceInfoEyeShade::hrFs()
        {
            return -1;
        }

        double DeviceInfoEyeShade::peFs() {
            return -1;
        }

        double DeviceInfoEyeShade::prFs() {
            return -1;
        }

        double DeviceInfoEyeShade::peMinMv() {
            return -1;
        }

        double DeviceInfoEyeShade::peMaxMv() {
            return -1;
        }

        int DeviceInfoEyeShade::peMinVal() {
            return -1;
        }

        int DeviceInfoEyeShade::peMaxVal() {
            return -1;
        }


        //坐垫设备信息
        double DeviceInfoCushion::eegFs()
        {
            return -1;
        }

        double DeviceInfoCushion::eegVoltageGain()
        {
            return -1;
        }

        int DeviceInfoCushion::eegMinVal()
        {
            return 0;
        }

        int DeviceInfoCushion::eegMaxVal()
        {
            return -1;
        }

        double DeviceInfoCushion::eegMinUv()
        {
            return -1;
        }

        double DeviceInfoCushion::eegMaxUv()
        {
            return -1;
        }

        int DeviceInfoCushion::eegDigits()
        {
            return -1;
        }

        double DeviceInfoCushion::hrFs()
        {
            return -1;
        }

        double DeviceInfoCushion::peFs() {
            return 125;
        }

        double DeviceInfoCushion::peMinMv() {
            return 0;
        }

        double DeviceInfoCushion::peMaxMv() {
            return 3000;
        }

        int DeviceInfoCushion::peMinVal() {
            return 0;
        }

        int DeviceInfoCushion::peMaxVal() {
            return 4095;
        }

        double DeviceInfoCushion::prFs() {
            return 25;
        }


    } // namespace define

} // namespace basic
